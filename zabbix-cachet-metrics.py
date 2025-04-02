#!/usr/bin/env python3
import sys
import os
import time
import json
import logging
import requests
from datetime import datetime, timedelta

try:
    if os.getenv("CONFIG_FILE") is not None:
        config_content = open(os.environ["CONFIG_FILE"])
    else:
        config_content = open(
            os.path.dirname(os.path.realpath(__file__)) + "/metrics-config.json"
        )
except Exception as e:
    print(e)
    sys.exit(0)
else:
    config_dict = json.load(config_content)

cachet_url = config_dict["service"]["cachet"]["url"]
cachet_api_key = config_dict["service"]["cachet"]["api_key"]
zbx_url = config_dict["service"]["zbx"]["url"]
zbx_user = config_dict["service"]["zbx"]["username"]
zbx_pass = config_dict["service"]["zbx"]["password"]
zbx_api_url = zbx_url + "/api_jsonrpc.php"
zbx_token = None
update_interval = config_dict["update_interval"]


def get_datetime():
    datetime_now = datetime.today().strftime("%Y-%m-%d %H:%M:00")
    datetime_now = datetime.strptime(datetime_now, "%Y-%m-%d %H:%M:%S")
    datetime_old = datetime_now - timedelta(minutes=1)

    timestamp_now = datetime_now.timestamp()
    timestamp_now = int(timestamp_now * 1000)

    timestamp_old = datetime_old.timestamp()
    timestamp_old = int(timestamp_old * 1000)

    output_dict = dict()
    output_dict["timestamp_now"] = str(timestamp_now)
    output_dict["timestamp_old"] = str(timestamp_old)
    return output_dict


def zbx_login(zbx_url, zbx_usrname, zbx_pawd):
    payload = {
        "jsonrpc": "2.0",
        "method": "user.login",
        "params": {"username": zbx_usrname, "password": zbx_pawd},
        "id": 1,
    }
    headers = {"content-type": "application/json"}
    req_run = requests.post(zbx_url, data=json.dumps(payload), headers=headers)
    req_content = json.loads(req_run.text)
    return req_content


def get_zbx_item_value(zbx_url, zbx_token, zbx_item_type, zbx_item_id):
    if zbx_item_type == "host":
        payload = {
            "jsonrpc": "2.0",
            "method": "history.get",
            "params": {
                "output": "extend",
                "history": 3,
                "itemids": zbx_item_id,
                "sortfield": "clock",
                "sortorder": "DESC",
                "limit": 1,
            },
            "auth": zbx_token,
            "id": 1,
        }
    else:
        payload = {
            "jsonrpc": "2.0",
            "method": "history.get",
            "params": {
                "output": "extend",
                "history": 0,
                "itemids": zbx_item_id,
                "sortfield": "clock",
                "sortorder": "DESC",
                "limit": 1,
            },
            "auth": zbx_token,
            "id": 1,
        }
    headers = {"content-type": "application/json"}
    print(json.dumps(payload))
    req_run = requests.post(zbx_url, data=json.dumps(payload), headers=headers)
    req_content = json.loads(req_run.text)
    print("Request content =========", req_content)
    if zbx_item_type == "host":
        if req_content["result"][0]["value"] != "0":
            req_value = 0
        else:
            req_value = 100
    else:
        req_value = req_content["result"][0]["value"]
    return str(req_value)


def get_number_of_visits(es_url, es_index_name, es_gte, es_lte):
    payload = {
        "size": 0,
        "_source": {"excludes": []},
        "aggs": {},
        "stored_fields": ["@timestamp"],
        "query": {
            "bool": {
                "must": [
                    {"match_all": {}},
                    {
                        "range": {
                            "@timestamp": {
                                "gte": es_gte,
                                "lte": es_lte,
                                "format": "epoch_millis",
                            }
                        }
                    },
                ]
            }
        },
    }
    headers = {"content-type": "application/json"}
    req_run = requests.post(
        es_url + es_index_name + "/_search", data=json.dumps(payload), headers=headers
    )
    req_content = json.loads(req_run.text)
    req_value = req_content["hits"]["total"]
    return str(req_value)


def cachethq_metrics_add_point(api_token, metric_id, metric_value, metric_timestamp):
    url = cachet_url + "/api/metrics/" + str(metric_id) + "/points"
    headers = {
        "Content-Type": "application/json",
        "accept": "application/json",
        "Authorization": f"Bearer {api_token}",
    }
    data = {"value": metric_value, "timestamp": int(metric_timestamp)}

    response = requests.post(url, headers=headers, data=json.dumps(data))

    print(response.status_code)
    print(response.json())
    return response


def run_zbx(config_json, time_now):
    zbx_type = config_json["type"]
    zbx_id = config_json["id"]
    metric_id = config_json["metric_id"]
    multiplier = config_json["multiplier"] or 1
    item_value_corrected = 0
    print("running zabbix")

    item_value = get_zbx_item_value(zbx_api_url, zbx_token, zbx_type, zbx_id)
    item_value_corrected = int(float(item_value) * multiplier)
    print("item value corrected: ", item_value_corrected)
    cachethq_metrics_add_point(
        cachet_api_key, metric_id, item_value_corrected, time_now[0:10]
    )


def run_es6(config_json, time_now, time_old):
    es6_api_url = config_json["es6_api_url"]
    es6_index = config_json["es6_index"]
    metric_id = config_json["metric_id"]

    item_value = get_number_of_visits(es6_api_url, es6_index, time_old, time_now)
    cachethq_metrics_add_point(cachet_api_key, metric_id, item_value, time_now[0:10])


def run_main():
    timestamp_dict = get_datetime()
    timestamp_now = timestamp_dict["timestamp_now"]
    timestamp_old = timestamp_dict["timestamp_old"]
    print("running main")
    for i in config_dict["config"]:
        print(i)
        if i["services"] == "zbx":
            run_zbx(i, timestamp_now)
        elif i["services"] == "es6":
            run_es6(i, timestamp_now, timestamp_old)


if __name__ == "__main__":
    exit_status = 0
    try:
        login_content = zbx_login(zbx_api_url, zbx_user, zbx_pass)
        zbx_token = login_content["result"]

        while True:
            run_main()
            time.sleep(update_interval)

    except KeyboardInterrupt:
        logging.info("Shutdown requested. See you.")
    except Exception as error:
        logging.error(error)
        exit_status = 1
    sys.exit(exit_status)
