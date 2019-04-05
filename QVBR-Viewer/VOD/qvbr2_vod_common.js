var data_version_string = "qvbr2";
var lastReportTimestamp = "";
var autoRefreshInterval = 5000;
var xhttp = new XMLHttpRequest();
var chartBitrate = undefined;
var chartConfig;
var report_config = "qvbr2_config.json"
var report_data_path = "";
var report_data_fname = "report_data.json";
var updateTimer = null;
var columns = 3;
var segment_delay = 2;
var segments_to_graph = 1000;
var videoURLs = ["", ""];
var hls = [null, null];
var videos = [null, null];
var hlsSegBuffered = [0, 0];
var hlsSegChanged = [0, 0];
var hlsBW = [0, 0];
var hlsBufferDelay = 2;
var report_length_seconds = 0;
var reportData;
var currentSegment = 0;
var allstreamnames = "";
var viewer_errors = new Array();

var getQueryString = function (field, url) {
    var href = url ? url : window.location.href;
    var reg = new RegExp('[?&]' + field + '=([^&#]*)', 'i');
    var string = reg.exec(href);
    return string ? string[1] : null;
};

function pad(n, width, z) {
    z = z || '0';
    n = n + '';
    return n.length >= width ? n : new Array(width - n.length + 1).join(z) + n;
}

function getImgFilename(index) {
    var name = "./images/" + pad(index, 4) + ".jpg";
    return name;
}

function printAvgTable() {
    document.getElementById("output_avg").innerHTML = "";

    var averagestr = "<tr><th class=\"highlight\">Rate Control</th>";
    for (i = 0; i < reportData.streams.length; i++) {
        averagestr += "<th class=\"highlight\">" + reportData.streams[i].name + " (<a href=\"" + reportData.streams[i].videoURL + "\">url</a>)</th>";
    }
    averagestr += "</tr>";
    averagestr += "<tr><th>Average Bitrate</th>";

    for (i = 0; i < reportData.streams.length; i++) {
        averagestr += "<td>" + reportData.streams[i].bitrate + "mbps</td>";
    }
    averagestr += "</tr>";
    averagestr += "<tr><th>Savings</th><td></td>";
    for (i = 1; i < reportData.streams.length; i++) {
        averagestr += "<td class=\"highlight_text\">" + Math.round((reportData.streams[i].bitrate / reportData.streams[0].bitrate - 1) * 100) + "%</td>";
    }
    averagestr += "</tr>";
    document.getElementById("output_avg").innerHTML += averagestr;
}

function updateAutoRefresh() {
    var bAutoRefresh = true;
    if (bAutoRefresh) {
        updateData();
    }
    else {
        if (updateTimer)
            clearTimeout(updateTimer);
    }
}

function updateNavLinks(testname) {
    var link;
    link = document.getElementById("playerlink").href.split("?");
    document.getElementById("playerlink").href = link[0] + "?data=" + testname + "/" + "report_data.json";
    link = document.getElementById("thumbslink").href.split("?");
    document.getElementById("thumbslink").href = link[0] + "?data=" + testname + "/" + "report_data.json";
}

function parseConfigJSON(configData) {
    var tests = [];
    var test;
    for (test in configData.tests) {
        tests.push(configData.data_folder + "/" + test);
    }
    console.log(tests.join(","));
    return tests;
}

function onInit() {
    var url_param = getQueryString('data');
    if (url_param) {
        report_data_path = url_param;
        var url_tags = report_data_path.split("/");
        url_tags.pop();
        updateNavLinks(url_tags.join("/"));
        updateData();
    }
    else {
        // load from config
        xhttp.onreadystatechange = function () {
            if (this.readyState == 4 && this.status == 200) {
                var configData = JSON.parse(this.responseText);
                var tests = parseConfigJSON(configData);
                report_data_path = tests[0] + "/" + report_data_fname;
                updateNavLinks(tests[0]);
                updateData();
            }
        };

        xhttp.open("GET", report_config + "?t=" + Math.random(), true);
        xhttp.send();
    }
}
