"""Test harness: a private mosquitto (random port, random password, localhost only) + simulated boards.
The generated password lives only in this process's memory and child environments; it is never printed."""
import os, secrets, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
MACS = {"cosmic": "020000c05a11", "galactic": "0200006a1ac7", "stellar": "020000573e11"}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Broker:
    def __init__(self):
        self.exe = os.environ.get("MOSQUITTO") or shutil.which("mosquitto") or "/usr/sbin/mosquitto"
        self.dir = tempfile.mkdtemp(prefix="unicorn-broker-")
        self.port = free_port()
        self.user = "studio"
        self.password = secrets.token_urlsafe(18)
        pw = os.path.join(self.dir, "passwd")
        passwd_exe = os.path.join(os.path.dirname(self.exe).replace("/sbin", "/bin"), "mosquitto_passwd")
        if not os.path.exists(passwd_exe):
            passwd_exe = shutil.which("mosquitto_passwd")
        subprocess.run([passwd_exe, "-c", "-b", pw, self.user, self.password], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        conf = os.path.join(self.dir, "mosquitto.conf")
        with open(conf, "w") as f:
            f.write("listener %d 127.0.0.1\nallow_anonymous false\npassword_file %s\npersistence false\n"
                    "log_dest file %s\n" % (self.port, pw, os.path.join(self.dir, "broker.log")))
        self.proc = subprocess.Popen([self.exe, "-c", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("mosquitto did not start")

    def env(self):
        e = dict(os.environ)
        e["UNICORN_SIM_PW"] = self.password
        return e

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


class Board:
    def __init__(self, broker, kind, mac=None, flash=None):
        self.kind = kind
        self.mac = mac or MACS[kind]
        self.base = "unicorn/" + self.mac[-6:]
        self.flash = flash or tempfile.mkdtemp(prefix="unicorn-flash-%s-" % kind)
        self.log = open(os.path.join(self.flash + ".log"), "w")
        self.broker = broker
        self.start()

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-u", os.path.join(HERE, "board_sim.py"), "--board", self.kind, "--host", "127.0.0.1",
             "--port", str(self.broker.port), "--user", self.broker.user, "--password-env", "UNICORN_SIM_PW",
             "--mac", self.mac, "--flash", self.flash],
            env=self.broker.env(), stdout=self.log, stderr=subprocess.STDOUT)

    def kill(self):
        self.proc.kill()
        self.proc.wait()

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        self.log.close()
