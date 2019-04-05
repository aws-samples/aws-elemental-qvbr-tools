#!/usr/bin/env python
import boto3
import json
from datetime import datetime, timedelta
import time
import os
import io
import stat
import botocore
from botocore.client import ClientError
from boto3.dynamodb.conditions import Key, Attr
import sys
import decimal
import re

FFPROBE = './ffprobe'
FFMPEG = './ffmpeg'
TEMPDIR = '/tmp/'

ADHOC_REPORT = False

bIngest = True
bGenerateReport = False
FILEMODE = False  # False for SQS
REPORT_DURATION_HOUR= 0.5

QVBR_REPORT_FUNCTION = 'qvbr_report'
QVBR_STAT_FUNCTION   = 'qvbr_stat'

s3 = boto3.resource('s3')
dynamodb = boto3.resource('dynamodb')
lambdaclient = boto3.client('lambda')

reportprefix = 'qvbr2'
config_fname = 'qvbr2_config.json'

def get_environment_variables():
    global REPORT_DURATION_HOUR
    global QVBR_REPORT_FUNCTION
    global QVBR_STAT_FUNCTION

    if os.environ.get('REPORT_DURATION_HOUR') is not None:
        try:
            floatvalue = float(os.environ['REPORT_DURATION_HOUR'])
        except:
            floatvalue = float(REPORT_DURATION_HOUR)
        REPORT_DURATION_HOUR = floatvalue
        print('environment variable REPORT_DURATION_HOUR was found: {}'.format(REPORT_DURATION_HOUR))

    if os.environ.get('QVBR_REPORT_FUNCTION') is not None:
        QVBR_REPORT_FUNCTION = os.environ['QVBR_REPORT_FUNCTION']
        print('environment variable QVBR_REPORT_FUNCTION was found: {}'.format(QVBR_REPORT_FUNCTION))

    if os.environ.get('QVBR_STAT_FUNCTION') is not None:
        QVBR_STAT_FUNCTION = os.environ['QVBR_STAT_FUNCTION']
        print('environment variable QVBR_STAT_FUNCTION was found: {}'.format(QVBR_STAT_FUNCTION))

###################################################
# Helper Functions
#

# Helper class to convert a DynamoDB item to JSON.
class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)
        return super(DecimalEncoder, self).default(o)

def get_s3file(BUCKET_NAME, KEY, LOCALFILE):
    try:
        s3.Bucket(BUCKET_NAME).download_file(KEY, LOCALFILE)
        return True
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == "404":
            print(KEY+" does not exist. 404")
            return False
        else:
            raise

def get_s3jsonObj(BUCKET_NAME, KEY):
    try:
        f = io.BytesIO()
        s3.Bucket(BUCKET_NAME).download_fileobj(KEY, f)
        return json.loads(f.getvalue())
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == "404":
            print(KEY+" does not exist. 404")
            return {}
        else:
            raise

def delete_file(myfile):
    if os.path.isfile(myfile):
        os.remove(myfile)
        # print("Success: %s file was deleted" % myfile)
    else:    ## Show an error ##
        print("Error: %s file not found" % myfile)

def dynamo_queryall(table_name, hashkey, start_sort_value):
    try:
        keyConditionExpression = Key('testname').eq(hashkey) & Key('segment_id').gt(start_sort_value)
        response = dynamodb.Table(table_name).query(
            KeyConditionExpression=keyConditionExpression
        )
        items = response['Items']
        while ('LastEvaluatedKey' in response):
            response = dynamodb.Table(table_name).query(
                KeyConditionExpression=keyConditionExpression,
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            items += response['Items']
        return items
    except botocore.exceptions.ClientError as e:
        print(table_name+" "+hashkey+" query error:"+e.response['Error']['Message'])
        return []



###################################################
# Create and Return report object
#
def generate_report(report_name, config_data, timestamp):

    data = {
        'version': reportprefix,
        'begin_timestamp': 0,
        'timestamp': 0,
        'window_seconds': 0,
        'heading1': config_data['heading1'],
        'heading2': config_data['heading2'],
        'streams': [],
        'thumb_tag': "",
        'thumbs': []
    }

    measurement_window = 0.0

    start_time = (datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=(REPORT_DURATION_HOUR*-1))).isoformat()[0:19]
    #print(start_time)

    # For each stream
    #    find max measurement_window, average bitrate, add bitrate, and get thumbnails for first stream
    #
    prefix_to_index = {}

    # loop through streams to set up data structure and init values
    for i in range(len(config_data['streams'])):
        stream_data = {
            'name': config_data['streams'][i]['label'],
            'videoURL': config_data['streams'][i]['videoURL'],
            'segment_bitrates': [],
            'bitrate': 0
        }
        data['streams'].append(stream_data)
        prefix_to_index[config_data['streams'][i]['stream_prefix']] = i

    # load dynamodb data (for all streams)
    items = dynamo_queryall(config_data['dynamo_table'], report_name, start_time)

    if (len(items)>0):
        # get global data
        first_segment_tags = items[0]['segment_id'].split('_')
        data['thumb_tag'] = items[0]['info']['thumbnail_tag']
        data['begin_timestamp'] = first_segment_tags[0]
        data['timestamp'] = items[-1]['segment_id'].split('_')[0]

        # init from first item
        bitrates = []
        durations = []
        tuplefound = []
        for i in range(len(config_data['streams'])):
            tuplefound.append(False)
            bitrates.append(long(0))
            durations.append(long(0))
        tupleSeq = int(first_segment_tags[2])

        #  consecutive item with same sequence number make up a tuple
        for item in items:
            item_idx     = prefix_to_index.get(item['segment_id'].split('_')[1], -1)
            item_seqnum  = int(item['segment_id'].split('_')[2])
            item_bitrate = item['info']['bitrate']
            item_duration = item['info']['duration_sec']
            if (item_idx < 0):
                continue
            #print ("tubleSeq:%i" % tupleSeq)
            #print ("stream:%s item_seq:%i bitrate:%s duration:%s" % (item_idx, item_seqnum, item_bitrate, item_duration))

            # ready to start new tuple.  fill in missing values with 0
            if (item_seqnum != tupleSeq):
                for j in range(len(config_data['streams'])):
                    if not tuplefound[j]:
                        data['streams'][j]['segment_bitrates'].append(float(0))
                        print ('%s: fill %i[%i]' % (item['segment_id'],j, tupleSeq))
                        if (j==0):
                            data['thumbs'].append(item['info']['thumbnail_file'])
                    tuplefound[j] = False
                tupleSeq = item_seqnum

            # still filling out tuple
            if (item_seqnum == tupleSeq):
                data['streams'][item_idx]['segment_bitrates'].append(float(item_bitrate))
                #print('save %i[%i]' % (item_idx, tupleSeq))
                # convert float to long to avoid numerical stability issue for sum of large set of small numbers
                bitrates[item_idx] += long(item_bitrate*item_duration*1000)
                durations[item_idx] += long(item_duration*1000)
                tuplefound[item_idx] = True

            # find max measurement window and get thumbs for first stream
            if (item_duration > measurement_window):
                measurement_window = item_duration
            if (item_idx==0):
                data['thumbs'].append(item['info']['thumbnail_file'])

        for i in range(len(config_data['streams'])):
            if (durations[i] > 0):
                data['streams'][i]['bitrate'] = round(float(bitrates[i]) / float(durations[i]),2)
            else:
                data['streams'][i]['bitrate'] = 0
        data['window_seconds'] = float(measurement_window)
    else:
        return {}

    return data

####################################################
# Lambda entry point function
#
def lambda_handler(event, context):

    get_environment_variables();

    temp_ts_file = TEMPDIR+'temp.ts'
    temp_jpg_file = TEMPDIR+'temp.jpg'

    segment_video_es_size = 0
    segment_duration = 0.0
    segment_bitrate = 0.0

    # Get s3 file object info
    try:
        bucketname = event['Records'][0]['s3']['bucket']['name']
        s3_filekey = event['Records'][0]['s3']['object']['key']
        bucket = s3.Bucket(bucketname)
    except:
        print ('Could not get ' + s3_filekey + ' info')
        return

    # split key tags: qvbr-rootname/report-name/stream-prefix_stream-segment.ts
    key_tags = s3_filekey.split(".")[0].split("/")
    qvbr_rootname = key_tags[0]
    report_name = key_tags[1]
    stream_tags = key_tags[-1].split("_")
    stream_prefix = stream_tags[0]
    stream_segment = stream_tags[-1]
    segment_time  = '%s-%s-%sT%s:%s:%s' % (stream_tags[-2][0:4],stream_tags[-2][4:6],stream_tags[-2][6:8],stream_tags[-2][9:11],stream_tags[-2][11:13],stream_tags[-2][13:15])
    stream_thumb_tag = qvbr_rootname+'/'+report_name+'/'+'images/'
    stream_thumb_fname = segment_time.split(".")[0]+'.jpg'
    stream_thumb_key = stream_thumb_tag + stream_thumb_fname
    report_json_key = qvbr_rootname+'/'+report_name+'/report_data.json'
    trigger_json_key = qvbr_rootname+'/'+report_name+'/report_trigger.json'

    # extract stream prefix and read config XML - this code assumes config file is utf-8
    # config_key = qvbr_rootname+"/" + reportprefix + "_"+report_name+".json"
    # config_data = get_s3jsonObj(bucketname, config_key)
    config_key = config_fname
    config_obj = get_s3jsonObj(bucketname, config_key)
    if not bool(config_obj):
        print 'Could not load '+config_key
        return
    config_data = config_obj['tests'][report_name];
    if not bool(config_data):
        print 'Could not load '+report_name
        return
    print(config_data)

    # download s3 TS file
    if (get_s3file(bucketname, s3_filekey, temp_ts_file)):

        # read video ES bitrate
        command = FFPROBE + ' -loglevel quiet -show_entries packet=duration_time,size -select_streams v:0 -of json -i ' + temp_ts_file
        #print (command)
        output_ffprobe = os.popen(command).read()
        ffprobe_json = json.loads(output_ffprobe)
        for frame in ffprobe_json['packets']:
            segment_video_es_size += int(frame['size'])
            segment_duration += float(frame['duration_time'])
        segment_bitrate = segment_video_es_size * 8 / segment_duration / 1000000
        #print '%s %s %d %0.2f %0.0f' % (event['Records'][0]['s3']['object']['key'], event['Records'][0]['s3']['object']['size'], segment_video_es_size, segment_duration, segment_bitrate)

        # if this is first stream, output first frame as jpg image and upload to s3
        if (stream_prefix == config_data['streams'][0]["stream_prefix"]):
            command = FFMPEG + ' -hide_banner -nostats -loglevel error -y -i ' + temp_ts_file + ' -vframes 1 -s 120x68 ' + temp_jpg_file
            #print(command)
            output_ffmpeg = os.popen(command).read()
            if bIngest:
                bucket.upload_file(temp_jpg_file, stream_thumb_key)

        # create and upload dynmo DB item
        dynamo_item = {
           'testname':  report_name,
           'segment_id': segment_time + "_" + stream_prefix + "_" + stream_segment,
           'bucket': bucketname,
           's3root': qvbr_rootname,
           'info': {
              'bitrate': decimal.Decimal("%.2f" % segment_bitrate),
              'duration_sec': decimal.Decimal("%.0f" % segment_duration),
              'thumbnail_tag': stream_thumb_tag,
              'thumbnail_file': stream_thumb_fname
           }
        }

        print(config_data)

        # print(dynamo_item)
        if bIngest:
            dynamodb.Table(config_data['dynamo_table']).put_item(Item=dynamo_item)

        # Only trigger report for 1st stream in set
        if (FILEMODE or (stream_prefix == config_data['streams'][0]["stream_prefix"])):
            # trigger short-term report
            trigger_data = {
                'bucket': bucketname,
                'qvbr_rootname': qvbr_rootname,
                'report_name': report_name,
                'config_data': config_data,
                'timestamp':   segment_time
            }
            lambdaclient.invoke(
                FunctionName=QVBR_REPORT_FUNCTION,
                InvocationType='Event',
                Payload=json.dumps(trigger_data, cls=DecimalEncoder)
            )

            # trigger long-term stats
            time_tag = segment_time.split(':')
            minute = int(time_tag[1])
            second = int(time_tag[2])
            if (minute == config_data['stat_trigger_minute']) and (second < config_data['segment_duration_sec']):
                stat_trigger = {
                    'bucket': bucketname,
                    'qvbr_rootname': qvbr_rootname,
                    'report_name': report_name,
                    'config_data': config_data,
                    'timestamp':   time_tag[0]+':00:00'
                }
                lambdaclient.invoke(
                    FunctionName=QVBR_STAT_FUNCTION,
                    InvocationType='Event',
                    Payload=json.dumps(stat_trigger, cls=DecimalEncoder)
                )

        print '%s %s inserted' % (dynamo_item['testname'], dynamo_item['segment_id'])
        return

