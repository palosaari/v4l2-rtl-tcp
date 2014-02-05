#!/usr/bin/env python
# Copyright (C) 2014 Antti Palosaari <crope@iki.fi>
# License GPL

import argparse
import socket
import threading
import os
import ctypes
import fcntl
import platform

# V4L2 controls
V4L2_CTRL_CLASS_USER    = 0x00980000;  # Old-style 'user' controls
# User-class control IDs
V4L2_CID_BASE           = (V4L2_CTRL_CLASS_USER | 0x900);
V4L2_CID_USER_BASE      = V4L2_CID_BASE;
CID_TUNER_BW            = ((V4L2_CID_USER_BASE | 0xf000) + 11);
CID_TUNER_GAIN          = ((V4L2_CID_USER_BASE | 0xf000) + 13);
        
V4L2_BUF_TYPE_SDR_CAPTURE   = 11;
V4L2_MEMORY_MMAP            = 1;

V4L2_TUNER_ADC              = 4;
V4L2_TUNER_RF               = 5;

PROT_READ  = 0x1;        # page can be read
PROT_WRITE = 0x2;        # page can be written

MAP_SHARED = 0x01;       # Share changes
        
O_RDWR      = 0x0002;    # open for reading and writing
O_NONBLOCK  = 0x0004;    # no delay

# V4L2 pixformat fourcc
V4L2_SDR_FMT_CU8     = ord('C') << 0 | ord('U') << 8 | ord('0') << 16 | ord('8') << 24;
V4L2_SDR_FMT_CU16LE  = ord('C') << 0 | ord('U') << 8 | ord('1') << 16 | ord('6') << 24;

# RTL TCP commands
CMD_SET_FREQ              = 0x01;
CMD_SET_SAMPLE_RATE       = 0x02;
CMD_SET_TUNER_GAIN_MODE   = 0x03;
CMD_SET_GAIN              = 0x04;
CMD_SET_FREQ_COR          = 0x05;
CMD_SET_AGC_MODE          = 0x08;
CMD_SET_TUNER_GAIN_INDEX  = 0x0d;

# V4L2 API datatypes

# 32/64 bit alignment
if (platform.machine().endswith('64')):
    ctype_alignment = 8
else:
    ctype_alignment = 4

class v4l2_format_sdr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("pixelformat", ctypes.c_uint32),
        ("reserved", 28 * ctypes.c_ubyte)
    ]

class v4l2_format(ctypes.Structure):
    class _u(ctypes.Union):
        _fields_ = [
            ("sdr", v4l2_format_sdr),
            ("raw_data", ctypes.c_char * 200),
        ]

    _fields_ = [
        ("type", ctypes.c_uint32),
        ("___padding", (ctype_alignment - 4) * ctypes.c_ubyte),
        ("fmt", _u),
    ]

class v4l2_frequency(ctypes.Structure):
    _fields_ = [
        ("tuner", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("frequency", ctypes.c_uint32),
        ("reserved", 8 * ctypes.c_uint32)
    ]

# V4L2 IOCTLs
_IOC_NRBITS   = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS  = 2

_IOC_NRSHIFT   = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT  = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE  = 0
_IOC_WRITE = 1
_IOC_READ  = 2

_IOC  = lambda d, t, nr, size: (d << _IOC_DIRSHIFT) | (ord(t) << _IOC_TYPESHIFT) | \
                               (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT)
_IO   = lambda t, nr:       _IOC(_IOC_NONE, t, nr, 0)
_IOR  = lambda t, nr, size: _IOC(_IOC_READ, t, nr, ctypes.sizeof(size))
_IOW  = lambda t, nr, size: _IOC(_IOC_WRITE, t, nr, ctypes.sizeof(size))
_IOWR = lambda t, nr, size: _IOC(_IOC_READ | _IOC_WRITE, t, nr, ctypes.sizeof(size))

VIDIOC_S_FMT                    = _IOWR('V', 5, v4l2_format)
VIDIOC_S_FREQUENCY              = _IOW('V', 57, v4l2_frequency)

thread_running = False;
sdr_device = '/dev/swradio0';

class StreamingThread(threading.Thread):
    def __init__(self, conn):
        threading.Thread.__init__(self)
        self.conn = conn
    def run(self):
        global thread_running
        print ("Starting streaming thread...")
        self.fd = open(sdr_device, 'rb')
        while thread_running:
            try:
                self.conn.send(self.fd.read(262144))
            except socket.error:
                break
        self.fd.close()
        print ("Stopping streaming thread...")

def handle_command():
    sampling_rate_set = False
    global thread_running

    # open V4L2 SDR device
    fd = os.open(sdr_device, os.O_RDWR | os.O_NONBLOCK)
    if (fd < 0):
        print ('SDR device open failed'), fd
        return

    # select stream format
    arg = v4l2_format()
    arg.type = V4L2_BUF_TYPE_SDR_CAPTURE
    arg.fmt.sdr.pixelformat = V4L2_SDR_FMT_CU8
    fcntl.ioctl(fd, VIDIOC_S_FMT, arg)

    # start TCP server
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 1234)) # localhost:1234
    s.listen(1)
    print ("Server waiting for connection on port 1234")

    while True:
        conn, addr = s.accept()
        print ('Client connected by'), addr
        # say hello to client!
        # 0-3 magic, 4-7 tuner type, 8-11 gain count
        conn.send('RTL0\x00\x00\x00\x01\x00\x00\x00\xff')

        # thread for data streaming
        thread = StreamingThread(conn)

        while conn:
            # wait command from client
            data = conn.recv(1024)
            if not data:
                break
            print (':'.join(x.encode('hex') for x in data))

            # extract RTL TCP command and value
            cmd = ord(data[0])
            val = ord(data[1]) << 24 | ord(data[2]) << 16 | ord(data[3]) << 8 | ord(data[4]) << 0
            if (cmd == CMD_SET_FREQ):
                print ('CMD_SET_FREQ'), val
                arg = v4l2_frequency()
                arg.tuner = 1
                arg.type = V4L2_TUNER_RF
                arg.frequency = val
                fcntl.ioctl(fd, VIDIOC_S_FREQUENCY, arg)
            elif (cmd == CMD_SET_SAMPLE_RATE):
                print ('CMD_SET_SAMPLE_RATE'), val
                arg = v4l2_frequency()
                arg.tuner = 0
                arg.type = V4L2_TUNER_ADC
                arg.frequency = val
                fcntl.ioctl(fd, VIDIOC_S_FREQUENCY, arg)
                sampling_rate_set = True
            elif (cmd == CMD_SET_TUNER_GAIN_MODE):
                print ('CMD_SET_TUNER_GAIN_MODE'), val
            elif (cmd == CMD_SET_GAIN):
                print ('CMD_SET_GAIN'), val
            elif (cmd == CMD_SET_FREQ_COR):
                print ('CMD_SET_FREQ_COR'), val
            elif (cmd == CMD_SET_AGC_MODE):
                print ('CMD_SET_AGC_MODE'), val
            elif (cmd == CMD_SET_TUNER_GAIN_INDEX):
                print ('CMD_SET_TUNER_GAIN_INDEX'), val

            if (not thread_running and sampling_rate_set):
                thread_running = True
                thread.start()

        # terminate thread
        thread_running = False
        sampling_rate_set = False
        thread.join()

    conn.close()
    os.close(fd)
    
def main():
    parser = argparse.ArgumentParser(description='V4L RTL TCP', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-d', '--device', type = argparse.FileType('r'), default = '/dev/swradio0', help = 'SDR device')
    args = parser.parse_args()
    args.device.close()
#    print (args)

    handle_command()

if __name__ == "__main__":
    main()
