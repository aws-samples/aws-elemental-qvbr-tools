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

# temp folder for running in lambda
TEMPDIR = '/tmp/'

# environment variables
REPORT_DURATION_HOUR=(7*24.0)

# config constants
OUTPUT_FNAME = 'stat_data.json'
REPORT_SUMMARY_HOUR_UNIT=1.0
REPORT_THUMBS_PER_UNIT=10
DEBUG=False

# file mode running
_report_time = '2018-01-13T00:00:00'
_report_name = 'test2'
_bucketname = 'bucketname'
_OUTPUT_FNAME = 'stat_data_all.json'

s3 = boto3.resource('s3')
dynamodb = boto3.resource('dynamodb')

# report version string
reportprefix = 'qvbr2'
config_fname = 'qvbr2_config.json'

def get_environment_variables():
    # global CONFIG_KEY
    # if os.environ.get('CONFIG_KEY') is not None:
    #     CONFIG_KEY = os.environ['CONFIG_KEY']
    #     print('environment variable CONFIG_KEY was found: {}'.format(CONFIG_KEY))

    global REPORT_DURATION_HOUR
    if os.environ.get('REPORT_DURATION_HOUR') is not None:
        try:
            floatvalue = float(os.environ['REPORT_DURATION_HOUR'])
        except:
            floatvalue = float(REPORT_DURATION_HOUR)
        REPORT_DURATION_HOUR = floatvalue
        print('environment variable REPORT_DURATION_HOUR was found: {}'.format(REPORT_DURATION_HOUR))



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

def dynamo_queryall(table_name, hashkey, start_sort_value, slow):
    try:
        keyConditionExpression = Key('testname').eq(hashkey) & Key('segment_id').gt(start_sort_value)
        response = dynamodb.Table(table_name).query(
            KeyConditionExpression=keyConditionExpression
        )
        items = response['Items']
        while ('LastEvaluatedKey' in response):
            if (slow):
                time.sleep(1)
                print ("read %i items" % (len(items)))
            response = dynamodb.Table(table_name).query(
                KeyConditionExpression=keyConditionExpression,
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            items += response['Items']
        return items
    except botocore.exceptions.ClientError as e:
        print(table_name+" "+hashkey+" query error:"+e.response['Error']['Message'])
        return []

#items, lastKey = dynamo_query(table_name, hashkey, start_sort_value, slow, lastEvaluatedKey)
# while (len(items)):
#    Process items
#    if (lastKey):
#       items = dyanmo_query(table_name, hashkey, start_sort_value, slow, lastKey)

def dynamo_query(table_name, hashkey, start_sort_value, lastKey):
    try:
        keyConditionExpression = Key('testname').eq(hashkey) & Key('segment_id').gt(start_sort_value)
        if (lastKey):
            response = dynamodb.Table(table_name).query(
                KeyConditionExpression=keyConditionExpression,
                ExclusiveStartKey=lastKey
            )
        else:
            response = dynamodb.Table(table_name).query(
                KeyConditionExpression=keyConditionExpression
            )
        items = response['Items']
        if ('LastEvaluatedKey' in response):
            lastKey = response['LastEvaluatedKey']
        else:
            lastKey = ""

        print ("Query retrieved %i items" % len(items))
        return items, lastKey
    except botocore.exceptions.ClientError as e:
        print(table_name+" "+hashkey+" query error:"+e.response['Error']['Message'])
        return []


###################################################
# Create and Return report object
#
def generate_stat_report(report_name, config_data, start_time):

    data = {
        'version': reportprefix,
        'begin_timestamp': 0,
        'timestamp': 0,
        'window_seconds': 0,
        'heading1': config_data['heading1'],
        'heading2': config_data['heading2'],
        'streams': [],
        'thumb_tag': "",
        'thumbs': [],
        'extendedInfos': []
    }

    measurement_window = REPORT_SUMMARY_HOUR_UNIT*3600;
    print(start_time)

    # For each stream
    #    calculate overall average bitraets, REPORT_SUMMARY_HOUR_UNIT average bitrates, and get thumbnails from first stream
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
        data['streams'].append(stream_data.copy())
        prefix_to_index[config_data['streams'][i]['stream_prefix']] = i

    # load dynamodb data (for all streams)
    items, lastKey = dynamo_query(config_data['dynamo_table'], report_name, start_time, "")

    if (len(items)>0):
        # get global data
        first_segment_tags = items[0]['segment_id'].split('_')
        data['thumb_tag'] = items[0]['info']['thumbnail_tag']
        data['begin_timestamp'] = first_segment_tags[0][0:19]


        # init from first item
        nextStartTime = start_time
        while nextStartTime < data['begin_timestamp']:
            nextStartTime = (datetime.strptime(nextStartTime, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=REPORT_SUMMARY_HOUR_UNIT)).isoformat()[0:19]
        extendedInfo1 = {
            'window_start_time': first_segment_tags[0],
            'multi_thumbs': [],
            'base_data_url': ""
        }
        data['extendedInfos'].append(extendedInfo1.copy())
        data['thumbs'].append(items[0]['info']['thumbnail_file'])

        bitrates = []
        durations = []
        tuplefound = []

        window_bitrates = []
        window_durations = []
        window_idx = 0
        thumb_idx = 0

        for i in range(len(config_data['streams'])):
            tuplefound.append(False)
            bitrates.append(long(0))
            durations.append(long(0))
            window_bitrates.append(long(0))
            window_durations.append(long(0))
        tupleSeq = int(first_segment_tags[2])

        #  consecutive item with same sequence number make up a tuple
        while (len(items)>0):
            for index, item in enumerate(items):
                item_tags    = item['segment_id'].split('_');
                item_idx     = prefix_to_index.get(item_tags[1], -1)
                item_time    = item_tags[0][0:19]
                item_seqnum  = int(item_tags[2])
                item_bitrate = item['info']['bitrate']
                item_duration = item['info']['duration_sec']
                if (item_idx < 0):
                    continue
                #print ("tubleSeq:%i" % tupleSeq)
                #print ("stream:%s item_seq:%i bitrate:%s duration:%s" % (item_idx, item_seqnum, item_bitrate, item_duration))

                # ready to start new tuple.  fill in missing values with 0
                if (item_seqnum != tupleSeq) or (lastKey=="" and index == len(items)-1):
                    for j in range(len(config_data['streams'])):
                        if not tuplefound[j]:
                            #data['streams'][j]['segment_bitrates'].append(float(0))
                            print ('%s: fill %i[%i]' % (item['segment_id'],j, tupleSeq))
                            if (j==0):
                                #data['thumbs'].append(item['info']['thumbnail_file'])
                                print ('place holder')
                        tuplefound[j] = False
                    tupleSeq = item_seqnum

                    # check to see if we have advanced to next window
                    if (item_time > nextStartTime) or (lastKey=="" and index == len(items)-1):
                        print("New window time: %s item time: %s old window idx: %i" %(nextStartTime, item_time, window_idx))

                        for i in range(len(config_data['streams'])):
                            if (window_durations[i] > 10):
                                window_avg_bitrate = round(float(window_bitrates[i]) / float(window_durations[i]),2)
                                data['streams'][i]['segment_bitrates'].append(float(window_avg_bitrate))
                            else:
                                data['streams'][i]['segment_bitrates'].append(float(0))
                            window_bitrates[i] = 0
                            window_durations[i] = 0

                        if (item_time > nextStartTime):
                            data['thumbs'].append(item['info']['thumbnail_file'])
                            thumb_idx = 0
                            window_idx += 1
                            while nextStartTime < item_time:
                                nextStartTime = (datetime.strptime(nextStartTime, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=REPORT_SUMMARY_HOUR_UNIT)).isoformat()[0:19]
                            extendedInfo1 = {
                                'window_start_time': item_time,
                                'multi_thumbs': [],
                                'base_data_url': ""
                            }
                            data['extendedInfos'].append(extendedInfo1.copy())

                # still filling out tuple
                if (item_seqnum == tupleSeq):
                    #data['streams'][item_idx]['segment_bitrates'].append(float(item_bitrate))
                    #print('save %i[%i]' % (item_idx, tupleSeq))
                    # convert float to long to avoid numerical stability issue for sum of large set of small numbers
                    bitrates[item_idx] += long(item_bitrate*item_duration*1000)
                    durations[item_idx] += long(item_duration*1000)
                    window_bitrates[item_idx] += long(item_bitrate*item_duration*1000)
                    window_durations[item_idx] += long(item_duration*1000)
                    tuplefound[item_idx] = True

                # get thumbs from first stream
                if (item_idx==0):
                    if (window_durations[item_idx]/1000 > thumb_idx * (REPORT_SUMMARY_HOUR_UNIT * 3600 / REPORT_THUMBS_PER_UNIT)):
                        data['extendedInfos'][window_idx]['multi_thumbs'].append(item['info']['thumbnail_file'])
                        thumb_idx += 1

                data['last_sequence'] = item_seqnum

            # load next page if there is more data
            if (lastKey):
                items, lastKey = dynamo_query(config_data['dynamo_table'], report_name, start_time, lastKey)
            else:
                # no more data
                data['timestamp'] = items[-1]['segment_id'].split('_')[0]
                items = []

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

    # config_key: 'bucket/qvbr-rootname/qvbr2_reportname.json'
    # print('event: '+json.dumps(event))
    # print('context: '+json.dumps(context.client_context))

    try:
        bucketname = event['bucket']
        qvbr_rootname = event['qvbr_rootname']
        report_name = event['report_name']
        config_data = event['config_data']
        report_time = event['timestamp'][0:19]
        report_json_key = qvbr_rootname+'/'+report_name+'/'+OUTPUT_FNAME
        bucket = s3.Bucket(bucketname)
    except:
        print ('Could not decode ' + json.dumps(event))
        return

    # generate report
    print(report_name + " " + report_time)
    start_time = (datetime.strptime(report_time, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=(REPORT_DURATION_HOUR*-1))).isoformat()[0:19]
    report_data = generate_stat_report(report_name, config_data, start_time)
    #print(json.dumps(report_data))

    if len(report_data):
        if (not DEBUG):
            report_data_buf = io.BytesIO(json.dumps(report_data, separators=(',',':')))
        else:
            report_data_buf = io.BytesIO(json.dumps(report_data, indent=2, separators=(',',': ')))
        bucket.Object(report_json_key).upload_fileobj(report_data_buf)
        print 'uploaded s3://'+bucketname+'/'+report_json_key
