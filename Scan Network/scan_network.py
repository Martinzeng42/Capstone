import socket
import struct
import subprocess
import ipaddress
import requests
from datetime import datetime
from scapy.all import ARP, Ether, srp


subnet = "192.168.0.0/24"  # change to match your LAN
smart_device_FLASKPORT = 3333
smart_device_TCPPORT = 4444
ready_devices = dict()
smart_devices = []

def get_subnet():
    # Find your IP and assume a /24 subnet
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    base = '.'.join(ip.split('.')[:-1]) + '.0/24'
    return base

def scan_network():
    target = get_subnet()
    arp = ARP(pdst=target)
    ether = Ether(dst="ff:ff:ff:ff:ff:ff")
    packet = ether/arp

    result = srp(packet, timeout=3, verbose=0)[0]
    devices = []
    for sent, received in result:
        devices.append({'ip': received.psrc, 'mac': received.hwsrc})
    return devices

def check_TCPdevice(ip, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1) 
            s.connect((ip, port))
            s.sendall(b"isready")

            response = s.recv(1024).decode().strip()
            print(f"[{ip}] Response: {response}")

            if response.lower() == "device is ready":
                print(f"Device at {ip} is ready.")
                return True
            else:
                print(f"Unexpected response from {ip}")
    except (socket.timeout, socket.error) as e:
        print(f"Could not connect to {ip}: {e}")
    return False

def send_TCPcommand(ip, port, command):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect((ip, port))
            s.sendall(command.encode())
            print(f"Sent '{command}' to {ip}:{port}")
    except Exception as e:
        print(f"Error sending command to {ip}: {e}")


def check_FLASKdevice(ip, port):
    try:
        URL = "http://{0}:{1}/{2}".format(ip, port, "ready")
        r = requests.get(URL, timeout = 1)
        content = r.text.strip()
        if (r.status_code == 200 and ("device is ready" in content.lower())):
            print(f"Device at {ip} is ready.")
            return True
    except:
        pass
    return False

def send_FLASKcommand(ip, port, command):
    try:
        URL = "http://{0}:{1}/{2}".format(ip, port, command)
        r = requests.get(URL, timeout = 2)
        print(f"Sent '{command}' to {ip}:{port}")
    except:
        pass

if __name__ == "__main__":
    devices = scan_network()
    if not devices:
        print("No devices found (network may be isolated).")
    else:
        print("Devices on your network:")
        for d in devices:
            ip = d["ip"]
            # Check TCP or Flask readiness
            
            tcp_ready = check_TCPdevice(ip, smart_device_TCPPORT)
            flask_ready = check_FLASKdevice(ip, smart_device_FLASKPORT)
            

            # if tcp_ready:
            #     print(f"TCP device found at {ip}")
            #     ready_devices[ip] = smart_device_TCPPORT
            #     smart_devices.append(ip)

            if flask_ready:
                print(f"Flask device found at {ip}")
                ready_devices[ip] = smart_device_FLASKPORT
                smart_devices.append(ip)

    # 🚀 User interaction loop
    while True:
        print("\nAvailable smart devices:")
        for i, ip in enumerate(smart_devices, start=1):
            print(f"{i}: {ip}:{ready_devices[ip]}")

        choice = input("Choose a device by number (or type 'exit'): ").strip()

        if choice.lower() == "exit":
            break

        try:
            index = int(choice) - 1
            selected_ip = smart_devices[index]
            selected_port = ready_devices[selected_ip]

            action = input("Enter command to send (turnon / turnoff): ").strip().lower()
            if action in ["turnon", "turnoff"]:
                if selected_port == smart_device_TCPPORT:
                    send_TCPcommand(selected_ip, selected_port, action)
                if selected_port == smart_device_FLASKPORT:
                    send_FLASKcommand(selected_ip, selected_port, action)
            else:
                print("Invalid command.")

        except (IndexError, ValueError):
            print("Invalid selection.")       



