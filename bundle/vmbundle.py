#!/usr/bin/env python

"""
Bundles, uploads and registers an image of the currently running OS.  Intended
to be used with VMs already running in the cloud, but can bundle from other 
sources using the --nocloud option.  Bundling from outside the cloud is not 
guaranteed to work and depends on proper configuration of the OS before 
bundling.
"""

from __future__ import division
from sys import argv
import os
import os.path
import boto
import utils
import vmcreate
import atexit
import time

GBs = 1024 * 1024 * 1024
MBs = 1024 * 1024
MOUNT_POINT_PREFIX = "/mnt/vmbundle"
DEFAULT_IMAGE_NAME = "filesystem"
DEFAULT_BUCKET_NAME = os.getenv("EC2_ACCESS_KEY").split(":")[1]

mount_point_created = False
mount_point = None
volume_created = False
volume_mounted = False
volume = None

@atexit.register
def cleanup():
	print("\n***** Cleaning up *****")
	if volume_mounted:
		utils.execute("umount " + mount_point)
	if volume_created:
		vmcreate.detach_and_delete_volume(volume)
	if mount_point_created:
		utils.execute("rm -rf " + mount_point)

def get_volume(size_in_GBs, instance, mount_point):
	global volume_created
	global volume_mounted
	global volume

	devices_before = set(utils.execute('ls /dev | grep -E vd[a-z][a-z]?')[0].split('\n'))

	print("\n***** Creating and attaching volume *****")
	volume = vmcreate.create_and_attach_volume(size_in_GBs, instance, '/dev/vdzz')
	volume_created = True	
	
	devices_after = set(utils.execute('ls /dev | grep -E vd[a-z][a-z]?')[0].split('\n'))
	new_devices = devices_after - devices_before

	if len(new_devices) != 1:
		print("Error attaching volume")
		exit(1)

	device = '/dev/' + new_devices.pop()

	print("\n***** Making filesystem on volume *****")
	utils.execute("mkfs -t ext3 %(device)s" % locals())

	print("\n***** Mounting volume to %(mount_point)s *****" % locals())
	utils.execute("mount %(device)s %(mount_point)s" % locals())
	volume_mounted = True

def wait_for_available(image_id):
	sleep_time = 10
	total_time = 0
	image = vmcreate.conn.get_image(image_id)
	print("\n***** Waiting for image to become available *****")

	while image.state != 'available':
		time.sleep(sleep_time)
		total_time += sleep_time
		image = vmcreate.conn.get_image(image_id)
		if total_time > 1800:
			print("\nTimed out waiting for image to become available")
			return False

	return True

def make_private(image_id):
	if not wait_for_available(image_id):
		print("\nUse 'euca-modify-image-attributes -l -r all %(image_id)s' to make the image private manually" % locals())
		return

	utils.execute("euca-modify-image-attribute -l -r all %(image_id)s" % locals())

if os.getuid() != 0:
	print("You need to run this script as root to bundle a VM.")
	exit(1)

if len(argv) > 1 and argv[1] == '--nocloud':
	cloud = False
else:
	cloud = True

custom_bucket_name = raw_input("Bucket name (%(DEFAULT_BUCKET_NAME)s): " % locals()).strip()
bucket_name = custom_bucket_name or DEFAULT_BUCKET_NAME

custom_image_name = raw_input("\nImage name (%(DEFAULT_IMAGE_NAME)s): " % locals()).strip()
image_name = custom_image_name or DEFAULT_IMAGE_NAME

kernel_name = ''
while True:
	custom_kernel_path = raw_input("\nKernel path (leave blank unless you have a custom kernel): ").strip()
	if custom_kernel_path and not os.path.exists(custom_kernel_path):
		print("%(custom_kernel_path)s does not exist" % locals())
	else:
		break

if custom_kernel_path:
	default_kernel_name = custom_kernel_path.split('/')[-1]
	custom_kernel_name = raw_input("\nKernel name (%(default_kernel_name)s): " % locals()).strip()
	kernel_name = custom_kernel_name or default_kernel_name

ramdisk_name = ''
while True:
	custom_ramdisk_path = raw_input("\nRamdisk path (leave blank unless you have a custom ramdisk): ").strip()
	if custom_ramdisk_path and not os.path.exists(custom_ramdisk_path):
		print("%(custom_ramdisk_path)s does not exist" % locals())
	else:
		break

if custom_ramdisk_path:
	default_ramdisk_name = custom_ramdisk_path.split('/')[-1]
	custom_ramdisk_name = raw_input("\nRamdisk name (%(default_ramdisk_name)s): " % locals()).strip()
	ramdisk_name = custom_ramdisk_name or default_ramdisk_name

is_private = raw_input("\nMake image(s) private so they're only visible to your project? (Y/n): ").strip()
if is_private == 'n' or is_private == 'N':
	private = False
else:
	private = True

fs = os.statvfs('/')
disk_size_in_GBs = int(round((fs.f_blocks * fs.f_frsize) / GBs))
disk_size_in_MBs = int(round((fs.f_blocks * fs.f_frsize) / MBs))

if cloud:
	print("\n***** Getting metadata *****")
	metadata = boto.utils.get_instance_metadata()
	instance_id = metadata['instance-id']
	instance = vmcreate.get_instance(instance_id)
	try:
		kernel_id = metadata['kernel-id']
	except KeyError:
		kernel_id = ''
	try:
		ramdisk_id = metadata['ramdisk-id']
	except KeyError:
		ramdisk_id = ''
else:
	kernel_id = ''
	ramdisk_id = ''

mount_point = MOUNT_POINT_PREFIX
i = 0
while os.path.exists(mount_point):
	mount_point = MOUNT_POINT_PREFIX + str(i)
	i += 1

print("\n***** Creating mount point %(mount_point)s *****" % locals())
utils.execute("mkdir -p %(mount_point)s" % locals())
mount_point_created = True

if fs.f_bfree <= fs.f_blocks * 2 / 3:
	if cloud:
		get_volume(disk_size_in_GBs * 2, instance, mount_point)
	else:
		print("Not enough space to bundle")
		exit(1)

if custom_kernel_path:
	print("\n***** Bundling kernel *****")
	utils.execute("euca-bundle-image -i %(custom_kernel_path)s -d %(mount_point)s --kernel true -p %(kernel_name)s" % locals())

	kernel_name += '.manifest.xml'

	print("\n***** Uploading kernel *****")
	utils.execute("euca-upload-bundle -b %(bucket_name)s -m %(mount_point)s/%(kernel_name)s" % locals())

	print("\n***** Registering kernel *****")
	kernel_id = utils.execute("euca-register %(bucket_name)s/%(kernel_name)s" % locals())[0].split()[1]

	if private:
		make_private(kernel_id)

if custom_ramdisk_path:
	print("\n***** Bundling ramdisk *****")
	utils.execute("euca-bundle-image -i %(custom_ramdisk_path)s -d %(mount_point)s --ramdisk true -p %(ramdisk_name)s" % locals())

	ramdisk_name += '.manifest.xml'

	print("\n***** Uploading ramdisk *****")
	utils.execute("euca-upload-bundle -b %(bucket_name)s -m %(mount_point)s/%(ramdisk_name)s" % locals())

	print("\n***** Registering ramdisk *****")
	ramdisk_id = utils.execute("euca-register %(bucket_name)s/%(ramdisk_name)s" % locals())[0].split()[1]

	if private:
		make_private(ramdisk_id)

try:
	utils.execute("rm -f /usr/NX/home/nx/.ssh/known_hosts")
	utils.execute("echo '' > /usr/NX/home/nx/.ssh/default.id_dsa.pub")
	utils.execute("echo '' > /usr/NX/home/nx/.ssh/authorized_keys2")
except:
	pass

dirs_to_exclude = "/mnt,/tmp,/root/.ssh,/home/ubuntu/.ssh,/etc/udev/rules.d,/var/lib/dhclient,/var/lib/dhcp3"
print("\n***** Excluding directories %(dirs_to_exclude)s *****" % locals())

utils.execute("sed -i 's/\S\+\s\+\/\s\+/\/dev\/vda \/ /' /etc/fstab")

print("\n***** Bundling filesystem *****")
kernel_opt = '' if kernel_id == '' else '--kernel ' + kernel_id
ramdisk_opt = '' if ramdisk_id == '' else '--ramdisk ' + ramdisk_id
utils.execute("euca-bundle-vol --no-inherit %(kernel_opt)s %(ramdisk_opt)s -d %(mount_point)s -r x86_64 -p %(image_name)s -s %(disk_size_in_MBs)s -e %(dirs_to_exclude)s" % locals())

image_name += '.manifest.xml'

print("\n***** Uploading filesystem *****")
utils.execute("euca-upload-bundle -b %(bucket_name)s -m %(mount_point)s/%(image_name)s" % locals())

print("\n***** Registering filesystem *****")
filesystem_id = utils.execute("euca-register %(bucket_name)s/%(image_name)s" % locals())[0].split()[1] 

if private:
	make_private(filesystem_id)
else:
	wait_for_available(filesystem_id)
