#!python3
import logging
import os
import subprocess
import json
import sys
import time

import paho.mqtt.client as MqttClient

mqtt_client = None
discovered = False
mqtt_connected = False

def set_power_state(host, user, password, power=True):
    global mqtt_client

    if power:
        power_state = "on"
    else:
        power_state = "off"
        mqtt_client.publish("ipmi/" + host.replace(".", "_") + "/get/sys_fan", "OFF", retain=True)
        mqtt_client.publish("ipmi/" + host.replace(".", "_") + "/get/power_switch", "OFF", retain=True)

    command = subprocess.check_output(['ipmitool', '-I', 'lanplus', '-H', host, '-U',
                                       user, '-P', password, 'chassis', 'power', power_state])
    command = str(command)

    if power:
        mqtt_client.publish("ipmi/" + host.replace(".", "_") + "/get/power_switch", "ON", retain=True)
        mqtt_client.publish("ipmi/" + host.replace(".", "_") + "/get/sys_fan", "ON", retain=True)
        time.sleep(10)
        set_fan_mode(host, user, password)

def set_fan_mode(host, user, password, auto=False):
    global mqtt_client
    #manual mode
    #ipmitool -I lanplus -H 10.82.1.92 -U root -P calvin raw 0x30 0x30 0x01 0x00
    if auto:
        auto_variable = "0x01"
        mqtt_client.publish("ipmi/" + host.replace(".", "_") + "/get/sys_fan_mode", "auto", retain=True)
        mqtt_client.publish("ipmi/" + host.replace(".", "_") + "/get/sys_fan_percent", "100", retain=True)
    else:
        auto_variable = "0x00"
        mqtt_client.publish("ipmi/" + host.replace(".", "_") + "/get/sys_fan_mode", "manual", retain=True)

    command = subprocess.check_output(['ipmitool', '-I', 'lanplus', '-H', host, '-U',
                                       user, '-P', password, 'raw', '0x30', '0x30', '0x01', auto_variable])
    command = str(command)

    if not auto:
        set_fan_speed(host, user, password)

def set_fan_speed(host, user, password, speed=20):
    #ipmitool -I lanplus -H 10.82.1.92 -U root -P calvin raw 0x30 0x30 0x02 0xff 0x28
    percent_hex = hex(speed)

    command = subprocess.check_output(['ipmitool', '-I', 'lanplus', '-H', host, '-U',
                                       user, '-P', password, 'raw', '0x30', '0x30', '0x02', '0xff', percent_hex])
    command = str(command)

    mqtt_client.publish("ipmi/" + host.replace(".", "_") + "/get/sys_fan_percent", speed, retain=True)

def get_device_details(host, user, password):
    #ipmitool -I lanplus -H 10.82.1.92 -U root -P calvin -c -v fru print 0
    command = subprocess.check_output(['ipmitool', '-I', 'lanplus', '-H', host, '-U',
                                       user, '-P', password, '-c', 'fru', 'print', '0'])
    command = str(command)
    data = {}
    for line in command.split("\\n"):
        bits = line.split(":")
        if "Product Manufacturer" in line or "Product Name" in line or "Product Serial" in line:
            data[bits[0].strip()] = bits[1].strip()

    return data

def get_power_status(host, user, password):
    #ipmitool -I lanplus -H 10.82.1.92 -U root -P calvin -c chassis power status
    command = subprocess.check_output(['ipmitool', '-I', 'lanplus', '-H', host, '-U',
                                       user, '-P', password, '-c', 'chassis', 'power', 'status'])
    command = str(command)
    if "Chassis Power is on" in command:
        return "on"
    elif "Unable to establish" in command:
        return "unavailable"
    else:
        return "off"

def get_sensor_results(host, user, password):
    #ipmitool -I lanplus -H 10.82.1.92 -U root -P calvin -c sdr entity 7.1
    command = subprocess.check_output(['ipmitool', '-I', 'lanplus', '-H', host, '-U',
                                       user, '-P', password, '-c', 'sdr', 'elist', 'all'])
    command = str(command)
    sensors = {}
    temp_counter = 1
    for line in command.split("\\n"):
        data = line.split(",")
        if "Fan" in line and "Redundancy" not in line:
            sensors[data[0]] = int(data[1])
            #Fan1 = 3320
        elif "Inlet Temp" in line or "Exhaust Temp" in line:
            temp = int(data[1])
            temp = (temp * 1.8000) + 32.00
            temp = round(temp, 2)
            sensors[data[0]] = temp
            #Inlet Temp = 69.81
        elif "Temp" in line:
            temp = int(data[1])
            temp = (temp * 1.8000) + 32.00
            temp = round(temp, 2)
            sensor_name = "Temp" + str(temp_counter)
            sensors[sensor_name] = temp
            temp_counter = temp_counter + 1
            # Temp1 = 69.81
        elif "Pwr Consumption" in line:
            sensors[data[0]] = int(data[1])
            #Pwr Consumption = 80

    return sensors

def create_server_objects(host, user, password):
    device_details = get_device_details(host, user, password)
    serial_number = device_details["Product Serial"]
    manufacturer = device_details["Product Manufacturer"]
    model = device_details["Product Name"]
    safe_host = host.replace(".", "_")

    device = {"cu": "https://" + host, "ids": [serial_number], "mf": manufacturer, "mdl": model, "name": model + " " + serial_number}
    sensor_base = {"avty_t": "ipmi/" + safe_host + "/get/sense_availability", "dev": device, "stat_cla": "measurement"}

    sensors = get_sensor_results(host, user, password)
    sensor_discovery = {"switch/ipmi_" + safe_host + "/power_switch": {"avty_t": "ipmi/" + safe_host + "/get/availability",
                              "dev": {"cu": "https://" + host, "ids": [serial_number], "mf": manufacturer,
                                      "mdl": model}, "name": "Power", "stat_t": "ipmi/" + safe_host + "/get/power_switch",
                                        "cmd_t": "ipmi/" + safe_host + "/set/power_switch",
                                         "uniq_id": safe_host + "_power_switch"},
                        "fan/ipmi_" + safe_host + "/sys_fan": {"avty_t": "ipmi/" + safe_host + "/get/availability",
                                                                  "dev": {"cu": "https://" + host,
                                                                          "ids": [serial_number], "mf": manufacturer,
                                                                          "mdl": model}, "name": "System Fans",
                                                                  "stat_t": "ipmi/" + safe_host + "/get/sys_fan",
                                                                  "cmd_t": "ipmi/" + safe_host + "/set/sys_fan",
                                                                  "pr_mode_stat_t": "ipmi/" + safe_host + "/get/sys_fan_mode",
                                                                  "pr_mode_cmd_t": "ipmi/" + safe_host + "/set/sys_fan_mode",
                                                                  "pr_modes": ["auto", "manual"],
                                                                  "pct_stat_t": "ipmi/" + safe_host + "/get/sys_fan_percent",
                                                                  "pct_cmd_t": "ipmi/" + safe_host + "/set/sys_fan_percent",
                                                                  "spd_rng_min": 10,
                                                                  "spd_rng_max": 100,
                                                                  "uniq_id": safe_host + "_sys_fan"}}
    sensor_values = {}

    for sensor_name in sensors.keys():
        sensor_value = sensors[sensor_name]
        clean_name = sensor_name.replace(" ", "_")
        if "Pwr" in sensor_name or "Power" in sensor_name:
            power_sensor = sensor_base.copy()
            power_sensor.update({"dev_cla": "power", "name": sensor_name, "stat_t": "ipmi/" + safe_host + "/get/" + clean_name, "uniq_id": safe_host + "_" + clean_name, "unit_of_meas": "W"})
            sensor_discovery["sensor/ipmi_" + safe_host + "/" + clean_name] = power_sensor
            sensor_values[clean_name] = sensor_value
        elif "Temp" in sensor_name:
            temp_sensor = sensor_base.copy()
            temp_sensor.update({"dev_cla": "temperature", "name": sensor_name, "stat_t": "ipmi/" + safe_host + "/get/" + clean_name, "uniq_id": safe_host + "_" + clean_name, "unit_of_meas": "°F"})
            sensor_discovery["sensor/ipmi_" + safe_host + "/" + clean_name] = temp_sensor
            sensor_values[clean_name] = sensor_value
        elif "Fan" in sensor_name:
            fan_sensor = sensor_base.copy()
            fan_sensor.update({"name": sensor_name, "icon": "mdi:fan",
                                         "stat_t": "ipmi/" + safe_host + "/get/" + clean_name,
                                         "uniq_id": safe_host + "_" + clean_name, "unit_of_meas": "rpm"})
            sensor_discovery["sensor/ipmi_" + safe_host + "/" + clean_name] = fan_sensor
            sensor_values[clean_name] = sensor_value

    return sensor_discovery, sensor_values

def publish_server_details(host, user, password):
    global discovered, mqtt_client
    #publish availability
    server_status = get_power_status(host, user, password)
    safe_host = host.replace(".", "_")
    if server_status == "unavailable":
        mqtt_client.publish("ipmi/" + safe_host + "/get/availability", "offline", retain=True)
        mqtt_client.publish("ipmi/" + safe_host + "/get/sense_availability", "offline", retain=True)
        return
    else:
        mqtt_client.publish("ipmi/" + safe_host + "/get/availability", "online", retain=True)

    if server_status == "off":
        sensor_values = {}
        sensor_values["power_switch"] = "OFF"
        sensor_values["sys_fan"] = "OFF"
        sensor_values["sys_fan_mode"] = "auto"
        sensor_values["sys_fan_percent"] = 100
        for sensor_value_name in sensor_values.keys():
            sensor_value = sensor_values[sensor_value_name]
            mqtt_client.publish("ipmi/" + safe_host + "/get/" + sensor_value_name, sensor_value, retain=True)
        mqtt_client.publish("ipmi/" + safe_host + "/get/sense_availability", "offline", retain=True)
        return

    sensor_discovery, sensor_values = create_server_objects(host, user, password)
    mqtt_client.publish("ipmi/" + safe_host + "/get/sense_availability", "online", retain=True)

    #publish power state
    sensor_values["power_switch"] = "ON"
    sensor_values["sys_fan"] = "ON"
    #sensor_values["sys_fan_mode"] = "manual"
    #sensor_values["sys_fan_percent"] = 20

    for sensor_value_name in sensor_values.keys():
        sensor_value = sensor_values[sensor_value_name]
        mqtt_client.publish("ipmi/" + safe_host + "/get/" + sensor_value_name, sensor_value, retain=True)

    if not discovered:
        for sensor_discovery_message in sensor_discovery.keys():
            discovery_message = sensor_discovery[sensor_discovery_message]
            mqtt_client.publish("homeassistant/" + sensor_discovery_message + "/config", json.dumps(discovery_message))
        discovered = True

def on_received_mqtt_message(client, user_data, message):
    payload = str(message.payload.decode("utf-8"))
    topic = str(message.topic)

    host = os.getenv("IPMI_SERVER", "")
    user = os.getenv("IPMI_USERNAME", "root")
    password = os.getenv("IPMI_PASSWORD", "calvin")

    if "power_switch" in topic:
        set_power_state(host, user, password, (payload == "ON"))
    elif "sys_fan_mode" in topic:
        set_fan_mode(host, user, password, (payload == "auto"))
    elif "sys_fan_percent" in topic:
        set_fan_speed(host, user, password, int(payload))

def on_mqtt_connected(client, user_data, flags, rc):
    global mqtt_connected
    logging.log(logging.INFO, "MQTT server is connected!")
    mqtt_connected = True

def on_mqtt_disconnected(client, user_data, flags, rc):
    global mqtt_connected
    logging.log(logging.CRITICAL, "MQTT server has disconnected!")
    mqtt_connected = False

def on_log(client, userdata, level, buff):
    print(buff)

def connect_mqtt(mqtt_server, mqtt_port, mqtt_client_id, mqtt_username=None, mqtt_password=None):
    global mqtt_client

    mqtt_client = MqttClient.Client(mqtt_client_id)
    mqtt_client.on_message = on_received_mqtt_message
    mqtt_client.on_connect = on_mqtt_connected
    mqtt_client.on_disconnect = on_mqtt_disconnected
    mqtt_client.on_log = on_log

    if mqtt_username is not None and mqtt_password is not None:
        mqtt_client.username_pw_set(mqtt_username, mqtt_password)

    try:
        print("Connecting to MQTT")
        mqtt_client.connect(mqtt_server, mqtt_port)
    except Exception as ex:
        print(ex)
        return False

    mqtt_client.loop_start()

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    host = os.getenv("IPMI_SERVER", "")
    username = os.getenv("IPMI_USERNAME", "root")
    password = os.getenv("IPMI_PASSWORD", "calvin")

    if host == "":
        print("Must provide environment variables:  IPMI_SERVER, MQTT_SERVER")
        sys.exit(1)

    client_id = "ipmi_" + host.replace(".", "_")

    print(connect_mqtt(os.getenv("MQTT_SERVER", "127.0.0.1"), int(os.getenv("MQTT_PORT", 1883)),
                 client_id, os.getenv("MQTT_USERNAME", None), os.getenv("MQTT_PASSWORD", None)))

    time.sleep(2)

    print("MQTT is subscribing topics...")
    mqtt_client.subscribe("ipmi/" + host.replace(".", "_") + "/set/power_switch", qos=0)
    mqtt_client.subscribe("ipmi/" + host.replace(".", "_") + "/set/sys_fan", qos=0)
    mqtt_client.subscribe("ipmi/" + host.replace(".", "_") + "/set/sys_fan_mode", qos=0)
    mqtt_client.subscribe("ipmi/" + host.replace(".", "_") + "/set/sys_fan_percent", qos=0)

    while True:
        try:
            publish_server_details(host, username, password)
        except:
            #ignore
            pass
        time.sleep(30)

    # Once we are ready to exit, stop MQTT
    mqtt_client.disconnect()
    mqtt_client.loop_stop()
    os.exit(0)
