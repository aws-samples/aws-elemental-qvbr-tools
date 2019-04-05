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
REPORT_DURATION_HOUR= 0.5

s3 = boto3.resource('s3')
dynamodb = boto3.resource('dynamodb')

reportprefix = 'qvbr2'
REPORT_FNAME = 'report_data.json'

def get_environment_variables():
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
            data['last_sequence'] = item_seqnum

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

    try:
        bucketname = event['bucket']
        qvbr_rootname = event['qvbr_rootname']
        report_name = event['report_name']
        config_data = event['config_data']
        timestamp = event['timestamp']
        report_json_key = qvbr_rootname+'/'+report_name+'/'+REPORT_FNAME
        bucket = s3.Bucket(bucketname)
    except:
        print ('Could not decode ' + json.dumps(event))
        return

    # download s3 TS file
    report_data = generate_report(report_name, config_data, timestamp)
    #print(json.dumps(report_data))
    if len(report_data):
        report_data_buf = io.BytesIO(json.dumps(report_data, separators=(',',':')))
        bucket.Object(report_json_key).upload_fileobj(report_data_buf)

    print timestamp + ' ' + report_json_key + ' generated'
    return
