#!/usr/bin/env python
# Copyright (C) 2014 Antti Palosaari <crope@iki.fi>
# License GPL

import argparse
import socket
import threading
import os
import ctypes
import fcntl

# V4L2 controls
V4L2_CTRL_CLASS_USER    = 0x00980000;  # Old-style 'user' controls
# User-class control IDs
V4L2_CID_BASE           = (V4L2_CTRL_CLASS_USER | 0x900);
V4L2_CID_USER_BASE      = V4L2_CID_BASE;
CID_TUNER_BW            = ((V4L2_CID_USER_BASE | 0xf000) + 11);
CID_TUNER_GAIN          = ((V4L2_CID_USER_BASE | 0xf000) + 13);
        
# V4L2 IOCTLs
CMD64_VIDIOC_DQBUF          = 0xc0585611;
CMD64_VIDIOC_S_EXT_CTRLS    = 0xc0205648;
CMD64_VIDIOC_S_FMT          = 0xc0d05605;
CMD64_VIDIOC_S_FREQUENCY    = 0x402c5639;
CMD64_VIDIOC_QBUF           = 0xc058560f;
CMD64_VIDIOC_QUERYBUF       = 0xc0585609;
CMD64_VIDIOC_QUERYCAP       = 0x80685600;
CMD64_VIDIOC_QUERYCTRL      = 0xc0445624;
CMD64_VIDIOC_QUERYSTD       = 0x8008563f;
CMD64_VIDIOC_REQBUFS        = 0xc0145608;
CMD64_VIDIOC_STREAMOFF      = 0x40045613;
CMD64_VIDIOC_STREAMON       = 0x40045612;
CMD64_VIDIOC_TRY_FMT        = 0xc0d05640;
        
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
V4L2_PIX_FMT_SDR_U8         = 0x38305544;
V4L2_PIX_FMT_SDR_U16LE      = 0x36315544;

# RTL TCP commands
CMD_SET_FREQ              = 0x01;
CMD_SET_SAMPLE_RATE       = 0x02;
CMD_SET_TUNER_GAIN_MODE   = 0x03;
CMD_SET_GAIN              = 0x04;
CMD_SET_FREQ_COR          = 0x05;
CMD_SET_AGC_MODE          = 0x08;
CMD_SET_TUNER_GAIN_INDEX  = 0x0d;

# V4L2 API datatypes
class v4l2_format_sdr(ctypes.Structure):
    """
struct v4l2_format_sdr {
	__u32				pixelformat;
	__u8				reserved[28];
} __attribute__ ((packed));
    """
    _fields_ = [("pixelformat", ctypes.c_uint32),
                ("reserved", 28 * ctypes.c_ubyte)]

class v4l2_format_union_fmt(ctypes.Union):
    _fields_ = [("sdr", v4l2_format_sdr),
                ("raw_data", 200 * ctypes.c_ubyte)]

class v4l2_format(ctypes.Structure):
    """
struct v4l2_format {
	__u32	 type;
	union {
		struct v4l2_pix_format		pix;     /* V4L2_BUF_TYPE_VIDEO_CAPTURE */
		struct v4l2_pix_format_mplane	pix_mp;  /* V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE */
		struct v4l2_window		win;     /* V4L2_BUF_TYPE_VIDEO_OVERLAY */
		struct v4l2_vbi_format		vbi;     /* V4L2_BUF_TYPE_VBI_CAPTURE */
		struct v4l2_sliced_vbi_format	sliced;  /* V4L2_BUF_TYPE_SLICED_VBI_CAPTURE */
		struct v4l2_format_sdr		sdr;     /* V4L2_BUF_TYPE_SDR_CAPTURE */
		__u8	raw_data[200];                   /* user-defined */
	} fmt;
};
	"""
    _fields_ = [("type", ctypes.c_uint32),
                ("padding", ctypes.c_uint32), # XXX: struct is not packed
                ("fmt", v4l2_format_union_fmt)]

class v4l2_frequency(ctypes.Structure):
    """
struct v4l2_frequency {
    __u32    tuner;
    __u32    type;    /* enum v4l2_tuner_type */
    __u32    frequency;
    __u32    reserved[8];
};
    """
    _fields_ = [("tuner", ctypes.c_uint32),
                ("type", ctypes.c_uint32),
                ("frequency", ctypes.c_uint32),
                ("reserved", 8 * ctypes.c_uint32)]

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
    fmt_sdr = v4l2_format_sdr(pixelformat = V4L2_PIX_FMT_SDR_U8)
    fmt_union = v4l2_format_union_fmt(sdr = fmt_sdr)
    arg = v4l2_format(type = V4L2_BUF_TYPE_SDR_CAPTURE, fmt = fmt_union)
    fcntl.ioctl(fd, CMD64_VIDIOC_S_FMT, ctypes.addressof(arg))

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
                arg = v4l2_frequency(tuner = 1, type = V4L2_TUNER_RF, frequency = val)
                fcntl.ioctl(fd, CMD64_VIDIOC_S_FREQUENCY, ctypes.addressof(arg))
            elif (cmd == CMD_SET_SAMPLE_RATE):
                print ('CMD_SET_SAMPLE_RATE'), val
                arg = v4l2_frequency(tuner = 0, type = V4L2_TUNER_ADC, frequency = val)
                fcntl.ioctl(fd, CMD64_VIDIOC_S_FREQUENCY, ctypes.addressof(arg))
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
