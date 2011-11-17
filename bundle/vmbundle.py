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
import vminit
import atexit

GBs = 1024 * 1024 * 1024
MBs = 1024 * 1024
MOUNT_POINT_PREFIX = "/mnt/vmbundle"
DEFAULT_IMAGE_NAME = "filesystem"
DEFAULT_BUCKET_NAME = "my-bucket"

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

def check_for_collisions(image_name, kernel_name, ramdisk_name):
	images = vmcreate.conn.get_all_images()

	for image in images:
		overwrite = ''

		if image.location == bucket_name + '/' + image_name + '.manifest.xml':
			overwrite = raw_input("\nImage already exists, overwrite? (y/N) ")
		elif image.location == bucket_name + '/' + kernel_name + '.manifest.xml':
			overwrite = raw_input("Kernel already exists, overwrite? (y/N) ")
		elif image.location == bucket_name + '/' + ramdisk_name + '.manifest.xml':
			overwrite = raw_input("Ramdisk already exists, overwrite? (y/N) ")
		else:
			continue
	
		if overwrite == 'y' or overwrite == 'Y':
			image.deregister()
		else:
			exit(0)

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


if not vminit.isRoot():
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

#make_private = raw_input("\nMake this image private so only members of your current project can see it? (Y/n): ").strip()
#if make_private == 'n' or make_private == 'N':
#	private = False
#else:
#	private = True
private = False

check_for_collisions(image_name, kernel_name, ramdisk_name)

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
		utils.execute("euca-modify-image-attribute -l -r all %(kernel_id)s" % locals())

if custom_ramdisk_path:
	print("\n***** Bundling ramdisk *****")
	utils.execute("euca-bundle-image -i %(custom_ramdisk_path)s -d %(mount_point)s --ramdisk true -p %(ramdisk_name)s" % locals())

	ramdisk_name += '.manifest.xml'

	print("\n***** Uploading ramdisk *****")
	utils.execute("euca-upload-bundle -b %(bucket_name)s -m %(mount_point)s/%(ramdisk_name)s" % locals())

	print("\n***** Registering ramdisk *****")
	ramdisk_id = utils.execute("euca-register %(bucket_name)s/%(ramdisk_name)s" % locals())[0].split()[1]

	if private:
		utils.execute("euca-modify-image-attribute -l -r all %(ramdisk_id)s" % locals())

try:
	utils.execute("rm -f /usr/NX/home/nx/.ssh/known_hosts")
	utils.execute("echo '' > /usr/NX/home/nx/.ssh/default.id_dsa.pub")
	utils.execute("echo '' > /usr/NX/home/nx/.ssh/authorized_keys2")
except:
	pass

dirs_to_exclude = "/mnt,/tmp,/root/.ssh,/home/ubuntu/.ssh,/etc/udev/rules.d,/var/lib/dhclient,/var/lib/dhcp3" % locals()
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
	utils.execute("euca-modify-image-attribute -l -r all %(filesystem_id)s" % locals())
