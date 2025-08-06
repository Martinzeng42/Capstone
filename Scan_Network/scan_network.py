import socket
import requests
from scapy.all import ARP, Ether, srp

class Scan_Network:
    def __init__(self, subnet=None, flask_port=3333, tcp_port=4444):
        self.subnet = subnet or self.get_subnet()
        self.smart_device_FLASKPORT = flask_port
        self.smart_device_TCPPORT = tcp_port
        self.ready_devices = dict()
        self.IP_smart_devices = []

    def get_subnet(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        base = '.'.join(ip.split('.')[:-1]) + '.0/24'
        return base

    def scan_network(self):
        arp = ARP(pdst=self.subnet)
        ether = Ether(dst="ff:ff:ff:ff:ff:ff")
        packet = ether/arp
        result = srp(packet, timeout=3, verbose=0)[0]
        devices = [{'ip': rcv.psrc, 'mac': rcv.hwsrc} for snd, rcv in result]
        return devices

    def check_TCPdevice(self, ip):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect((ip, self.smart_device_TCPPORT))
                s.sendall(b"isready")
                response = s.recv(1024).decode().strip()
                print(f"[{ip}] Response: {response}")
                return response.lower() == "device is ready"
        except (socket.timeout, socket.error) as e:
            print(f"Could not connect to {ip}: {e}")
        return False

    def send_TCPcommand(self, ip, command):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect((ip, self.smart_device_TCPPORT))
                s.sendall(command.encode())
                print(f"Sent '{command}' to {ip}:{self.smart_device_TCPPORT}")
        except Exception as e:
            print(f"Error sending command to {ip}: {e}")

    def check_FLASKdevice(self, ip):
        try:
            url = f"http://{ip}:{self.smart_device_FLASKPORT}/ready"
            r = requests.get(url, timeout=1)
            if r.status_code == 200 and "device is ready" in r.text.lower():
                print(f"Device at {ip} is ready.")
                return True
        except:
            pass
        return False

    def send_FLASKcommand(self, ip, command):
        try:
            url = f"http://{ip}:{self.smart_device_FLASKPORT}/{command}"
            r = requests.get(url, timeout=1)
            print(f"Sent '{command}' to {ip}:{self.smart_device_FLASKPORT}")
        except:
            pass

    def add_devices(self, ip, port):
        if (ip not in self.IP_smart_devices):
            self.ready_devices[ip] = port
            self.IP_smart_devices.append(ip)

    def get_devices_list(self):
        devices = self.scan_network()
        if not devices:
            print("No devices found (network may be isolated).")
        else:
            print("Devices on your network:")
            for d in devices:
                ip = d["ip"]
                
                # Check TCP or Flask readiness
                tcp_ready = self.check_TCPdevice(ip)
                flask_ready = self.check_FLASKdevice(ip)
                
                # TCP Connection
                if tcp_ready:
                    print(f"TCP device found at {ip}")
                    self.add_devices(ip, self.smart_device_TCPPORT)
                # Flask Connection
                elif flask_ready:
                    print(f"Flask device found at {ip}")
                    self.add_devices(ip, self.smart_device_FLASKPORT)
        return self.IP_smart_devices

    def get_protocol_IP(self, ip):
        return self.ready_devices[ip]

    def run(self):
        devices = self.get_devices_list()

        while True:
            print("\nAvailable smart devices:")
            for i, ip in enumerate(self.IP_smart_devices, start=1):
                print(f"{i}: {ip}:{self.ready_devices[ip]}")

            choice = input("Choose a device by number (or type 'exit'): ").strip()
            if choice.lower() == "exit":
                break

            try:
                index = int(choice) - 1
                selected_ip = self.IP_smart_devices[index]
                selected_port = self.ready_devices[selected_ip]
                action = input("Enter command to send (turnon / turnoff): ").strip().lower()

                if action in ["turnon", "turnoff"]:
                    if selected_port == self.smart_device_TCPPORT:
                        self.send_TCPcommand(selected_ip, action)
                    if selected_port == self.smart_device_FLASKPORT:
                        self.send_FLASKcommand(selected_ip, action)
                else:
                    print("Invalid command.")

            except (IndexError, ValueError):
                print("Invalid selection.")

if __name__ == "__main__":
    scan = Scan_Network()
    scan.run()