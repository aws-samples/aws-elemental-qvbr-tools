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
from botocore.vendored import requests
import sys
import copy

TEMPDIR = '/tmp/'

s3 = boto3.resource('s3')

###################################################
# resource files to be installed
#
files = [ "qvbr2_video_bitrate_player.html",
          "qvbr2_report_aws.html",
          "qvbr2_stats.html",
          "favicon.ico"]

###################################################
# Sample Config
#

sampleConfig = {
  "data_folder" : "data",
  "tests": {
    "test": {
      "stat_trigger_minute": 0,
      "dynamo_table": "",
      "heading1": "QVBR Viewer",
      "heading2": "Config: AVC 1080p CBR, QVBR 1080p Q8, QVBR 720p Q7, QVBR 720p Q6 ",
      "segment_duration_sec": 10,
      "streams": [
        {
          "stream_prefix": "avc-cbr",
          "label": "CBR",
          "videoURL": "test/avc-cbr.m3u8"
        },
        {
          "stream_prefix": "avc-qvbr8",
          "label": "QVBR Q8",
          "videoURL": "test/avc-qvbr8.m3u8"
        },
        {
          "stream_prefix": "avc-qvbr7",
          "label": "QVBR Q7",
          "videoURL": "test/avc-qvbr7.m3u8"
        },
        {
          "stream_prefix": "avc-qvbr6",
          "label": "QVBR Q6",
          "videoURL": "test/avc-qvbr6.m3u8"
        }
      ]
    }
  }
}

###################################################
# Helper Functions
#
# copies all objects in source_bucket/source_prefix into bucket/prefix
def copy_objects(source_bucket, source_prefix, bucket, prefix):
    client = boto3.client('s3')
    paginator = client.get_paginator('list_objects_v2')
    page_iterator = paginator.paginate(Bucket=source_bucket, Prefix=source_prefix)
    result = "none"
    try:    
      for key in {x['Key'] for page in page_iterator for x in page['Contents']}:
          dest_key = key.split('/')[-1]
          print("key " + key)
          print("dest_key " + dest_key)
          if dest_key != bucket and dest_key != "":
            print('copy {} to {}'.format(dest_key, bucket))
            result = client.copy_object(CopySource={'Bucket': source_bucket, 'Key': key}, Bucket=bucket, Key = dest_key)
    except Exception as e:
      print(e)
    return result

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

cfn_SUCCESS = "SUCCESS"
cfn_FAILED = "FAILED"

def cfn_send(event, context, responseStatus, responseData, physicalResourceId=None, noEcho=False):
    responseUrl = event['ResponseURL']

    print responseUrl

    responseBody = {}
    responseBody['Status'] = responseStatus
    responseBody['Reason'] = 'See the details in CloudWatch Log Stream: ' + context.log_stream_name
    responseBody['PhysicalResourceId'] = physicalResourceId or context.log_stream_name
    responseBody['StackId'] = event['StackId']
    responseBody['RequestId'] = event['RequestId']
    responseBody['LogicalResourceId'] = event['LogicalResourceId']
    responseBody['NoEcho'] = noEcho
    responseBody['Data'] = responseData

    json_responseBody = json.dumps(responseBody)

    print "Response body:\n" + json_responseBody

    headers = {
        'content-type' : '',
        'content-length' : str(len(json_responseBody))
    }

    try:
        response = requests.put(responseUrl,
                                data=json_responseBody,
                                headers=headers)
        print "Status code: " + response.reason
    except Exception as e:
        print "send(..) failed executing requests.put(..): " + str(e)

####################################################
# Lambda entry point function
#
def lambda_handler(event, context):
    try:
        print (json.dumps(event))

        if (event['RequestType'] != "Delete"):
            sourceS3Path = event['ResourceProperties']['sourceS3Path']
            srcBucket = event['ResourceProperties']['sourceS3Bucket']
            dstBucket = event['ResourceProperties']['s3bucket']
            cfDomain = event['ResourceProperties']['CloudFrontDomain']
            # copy source files to destination bucket
            # for obj in files:
            #   s3.meta.client.copy({'Bucket':srcBucket, 'Key':sourceS3Path+obj}, dstBucket, obj)
            copy_objects(srcBucket, sourceS3Path, dstBucket, "")

            # create sample config file and upload to destination bucket
            for test in sampleConfig['tests']:
                sampleConfig['tests'][test]['dynamo_table'] = event['ResourceProperties']['dynamoTablename']
                for stream in sampleConfig['tests'][test]['streams']:
                    url = stream['videoURL']
                    #stream['videoURL'] = "https://" + dstBucket + ".s3-" + event['ResourceProperties']['region'] + "/data/" + url
                    # use CloudFront
                    stream['videoURL'] = cfDomain + "/data/" + url

            configstring = json.dumps(sampleConfig, indent=2, separators=(',',': '))
            print(configstring)
            sampleConfig_buf = io.BytesIO(configstring)
            s3.Bucket(dstBucket).Object('qvbr2_config.json').upload_fileobj(sampleConfig_buf)

            cfn_send(event, context, cfn_SUCCESS, {}, "CustomResourcePhysicalID")
        else:  #delete
            cfn_send(event, context, cfn_SUCCESS, {}, "CustomResourcePhysicalID")

    except Exception as e:
        print(e)
        cfn_send(event, context, cfn_FAILED, {}, "CustomResourcePhysicalID")
