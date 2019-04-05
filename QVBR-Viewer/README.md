# AWS Elemental Quality-Defined Variable Bitrate (QVBR) Viewer

Quality-defined variable bitrate (QVBR) control is a new rate control mode that is currently available in the AWS Elemental MediaLive and AWS Elemental MediaConvert, as well as the AWS Elemental Live and AWS Elemental Server products. To learn more about QVBR, go [here](https://www.elemental.com/applications/what-quality-defined-variable-bitrate-qvbr-control).
And go [here](https://aws.amazon.com/blogs/media/how-to-use-qvbr-for-streaming-live-events-like-the-2018-aws-re-invent-keynotes/) to learn about how QVBR was used during the 2018 AWS re:Invent Keynote.

The visualization tool provided here ultimately produces a webpage that shows the comparative bitrate usage of the HLS videos generated using different rate control modes against QVBR in realtime. This then allows the user to see the bitrate savings one can get from using QVBR mode.

To launch the Live QVBR Viewer, go [here](Live/README.md).

To launch the VOD QVBR Viewer, go [here](VOD/README.md).