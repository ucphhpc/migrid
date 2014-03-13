#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# --- BEGIN_HEADER ---
#
# fileio - [insert a few words of module description on this line]
# Copyright (C) 2003-2014  The MiG Project lead by Brian Vinter
#
# This file is part of MiG.
#
# MiG is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# MiG is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
# -- END_HEADER ---
#

"""IO operations"""

from hashlib import md5
import fcntl
import os
import shutil
import time
import zipfile

from shared.serial import dump, load

def write_chunk(path, chunk, offset, logger, mode='r+b'):
    """Wrapper to handle writing of chunks with offset to path.
    Creates file first if it doesn't already exist.
    """
    logger.info('writing chunk to %s at offset %d' % (path, offset))

    # create dir and file if it does not exists

    (head, _) = os.path.split(path)
    if not os.path.isdir(head):
        try:
            os.mkdir(head)
        except Exception, err:
            logger.error('could not create dir %s' % err)
    if not os.path.isfile(path):
        try:
            open(path, "w").close()
        except Exception, err:
            logger.error('could not create file %s' % err)
    try:
        filehandle = open(path, mode)
        # Make sure we can write at requested position, filling if needed
        try:
            filehandle.seek(offset)
        except:
            filehandle.seek(0, 2)
            file_size = filehandle.tell()
            for _ in xrange(offset - file_size):
                filehandle.write('\0')
        logger.info('write %s chunk of size %d at position %d' % \
                     (path, len(chunk), filehandle.tell()))
        filehandle.write(chunk)
        filehandle.close()
        logger.debug('file chunk written: %s' % path)
        return True
    except Exception, err:
        logger.error('could not write %s chunk at %d: %s' % \
                     (path, offset, err))
        return False

def write_file(content, path, logger, mode='w'):
    """Wrapper to handle writing of contents to path"""
    logger.debug('writing file: %s' % path)

    # create dir if it does not exists

    (head, _) = os.path.split(path)
    if not os.path.isdir(head):
        try:
            logger.debug('making directory %s' % head)
            os.mkdir(head)
        except Exception, err:
            logger.error('could not create dir %s' % err)
    try:
        filehandle = open(path, mode)
        filehandle.write(content)
        filehandle.close()
        logger.debug('file written: %s' % path)
        return True
    except Exception, err:
        logger.error('could not write %s %s' % (path, err))
        return False


def get_file_size(path, logger):
    """Wrapper to handle getsize of path"""
    logger.debug('getsize on file: %s' % path)
    try:
        return os.path.getsize(path)
    except Exception, err:
        logger.error('could not get size for %s: %s' % (path, err))
        result = -1


def delete_file(path, logger):
    """Wrapper to handle deletion of path"""
    logger.debug('deleting file: %s' % path)
    if os.path.exists(path):
        try:
            os.remove(path)
            result = True
        except Exception, err:
            logger.error('could not delete %s %s' % (path, err))
            result = False
    else:
        logger.info('%s does not exist.' % path)
        result = False

    return result


def make_symlink(dest, src, logger):
    """Wrapper to make src a symlink to dest path"""
    try:
        logger.debug('creating symlink: %s %s' % (dest, src))
        os.symlink(dest, src)
    except Exception, err:
        logger.error('Could not create symlink %s' % err)
        return False
    return True


def filter_pickled_list(path, changes):
    """Filter pickled list on disk with provided changes where changes is a
    dictionary mapping existing list entries and the value to replace it with.
    """

    saved_list = load(path)
    saved_list = [changes.get(entry, entry) for entry in saved_list]
    dump(saved_list, path)
    return saved_list


def filter_pickled_dict(path, changes):
    """Filter pickled dictionary on disk with provided changes where changes
    is a dictionary mapping existing dictionary values to a value to replace
    it with.
    """

    saved_dict = load(path)
    for (key, val) in saved_dict.items():
        if val in changes.keys():
            saved_dict[key] = changes[val]
    dump(saved_dict, path)
    return saved_dict


def update_pickled_dict(path, changes):
    """Update pickled dictionary on disk with provided changes"""

    saved_dict = load(path)
    saved_dict.update(changes)
    dump(saved_dict, path)
    return saved_dict


def unpickle_and_change_status(path, newstatus, logger):
    """change status in the MiG server mRSL file"""

    changes = {}
    changes['STATUS'] = newstatus
    changes[newstatus + '_TIMESTAMP'] = time.gmtime()
    try:
        job_dict = update_pickled_dict(path, changes)
        logger.info('job status changed to %s: %s' % (newstatus,
                    path))
        return job_dict
    except Exception, err:
        logger.error('could not change job status to %s: %s %s'
                      % (newstatus, path, err))
        return False


def unpickle(path, logger):
    """Unpack pickled object in path"""
    try:
        job_dict = load(path)
        logger.debug('%s was unpickled successfully' % path)
        return job_dict
    except Exception, err:
        logger.error('%s could not be opened/unpickled! %s'
                      % (path, err))
        return False


def pickle(job_dict, path, logger):
    """Pack job_dict as pickled object in path"""
    try:
        dump(job_dict, path)
        logger.debug('pickle success: %s' % path)
        return True
    except Exception, err:
        logger.error('could not pickle: %s %s' % (path, err))
        return False


def send_message_to_grid_script(message, logger, configuration):
    """Write an instruction to the grid_script name pipe input"""
    try:
        filehandle = open(configuration.grid_stdin, 'a')
        fcntl.flock(filehandle.fileno(), fcntl.LOCK_EX)
        filehandle.write(message)
        filehandle.close()
        return True
    except Exception, err:
        print 'could not get exclusive access or write to grid_stdin!'
        logger.error('could not write "%s" to grid_stdin: %s' % \
                     (message, err))                     
        return False


def touch(filepath, timestamp=None):
    """Create or update timestamp for filepath"""
    try:
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            filehandle = open(filepath, 'r+w')
            i = filehandle.read(1)
            filehandle.seek(0, 0)
            filehandle.write(i)
            filehandle.close()
        else:
            open(filepath, 'w').close()

        if timestamp != None:

            # set timestamp to supplied value

            os.utime(filepath, (timestamp, timestamp))
    except Exception, err:

        print "could not touch file: '%s', Error: %s" % (filepath, err)
        return False


def remove_rec(dir_path, configuration):
    """
    Remove the given dir_path, and all subdirectories, recursively.
    This function sets the permissions on files and subdirectories
    before using shutil.rmtree, which is necessary when removing
    VGrid directories (wiki script and mercurial script).

    Returns Boolean to indicate success, writes messages to log.
    """

    try:
        if not os.path.isdir(dir_path):
            raise Exception("Directory %s does not exist" % dir_path)

        os.chmod(dir_path, 0777)

        # extend permissions top-down
        for root, dirs, files in os.walk(dir_path, topdown=True):
            for name in files:
                os.chmod(os.path.join(root, name), 0777)
            for name in dirs:
                os.chmod(os.path.join(root, name), 0777)
        shutil.rmtree(dir_path)

    except Exception, err:
        configuration.logger.error("Could not remove_rec %s: %s" % \
                                   (dir_path, err))
        return False

    return True

def move(src, dst):
    """Recursively move a file or directory from src to dst. This version works
    even in cases rename does not - e.g. for src and dst on different devices.
    """
    return shutil.move(src, dst)

def copy(src, dst):
    """Copy a file from src to dst where dst my be a directory"""
    return shutil.copy(src, dst)

def copy_file(src, dst, configuration):
    """Copy a file from src to dst where dst must be a new file path and
    the parent dir is created if necessary.
    """
    dst_dir = os.path.dirname(dst)
    try:
        os.makedirs(dst_dir)
    except:
        # probably already exists
        pass
    try:
        shutil.copy(src, dst)
    except Exception, exc:
        return (False, "copy failed: %s" % exc)
    return (True, "")

def copy_rec(src, dst, configuration):
    """Copy a dir recursively to dst where dst must be a new dir path and the
    parent dir is created if necessary.
    """
    dst_dir = os.path.dirname(dst)
    try:
        os.makedirs(dst_dir)
    except:
        # probably already exists
        pass
    try:
        shutil.copytree(src, dst)
    except Exception, exc:
        return (False, "copy failed: %s" % exc)
    return (True, "")

def write_zipfile(zip_path, paths, archive_base=''):
    """Write each of the files/dirs in paths to a zip file with zip_path.
    Given a non-empty archive_base string, that string will be used as the
    directory path of the archived files.
    """
    try:
        # Force compression
        zip_file = zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED)
        # Directory write is not supported - add each file manually
        for script in paths:

            # Replace real directory path with archive_base if specified

            if archive_base:
                archive_path = '%s/%s' % (archive_base, 
                        os.path.basename(script))
            else:
                archive_path = script
            zip_file.write(script, archive_path)
        zip_file.close()
        return (True, '')        
    except Exception, err:
        return (False, err)

def strip_dir(path):
    """Strip directory part of path for all known path formats. We can
    not simply use os.path.basename() as it doesn't work if client
    supplies, say, an absolute windows path.
    """

    if path.find(':') >= 0:

        # Windows absolute path - name is just right of rightmost backslash

        index = path.rfind('\\')
        name = path[index + 1:]
    else:
        name = os.path.basename(path)
    return name

def md5sum_file(path, chunk_size, max_chunks=-1):
    """Simple md5 hashing for checksumming of files inspired by  
    http://stackoverflow.com/questions/16799088/file-checksums-in-python
    Read at most max_chunks blocks of chunk_size (to avoid DoS) and checksum
    using md5.
    Any non-positive max_chunks value removes the size limit.  
    If max_chunks is positive the checksum will match that of the md5sum
    command for files smaller than max_chunks * chunk_size and for bigger
    files a partial checksum of the first chunk_size * max_chunks bytes will
    be returned.
    """
    checksum = md5()
    chunks_read = 0
    msg = ''
    try:
        file_fd = open(path, 'rb')
        while max_chunks < 1 or chunks_read < max_chunks:
            block = file_fd.read(chunk_size)
            if not block:
                break
            checksum.update(block)
            chunks_read += 1
        if file_fd.read(1):
            msg = ' (of first %d bytes)' % (chunk_size * max_chunks)
        return "%s%s" % (checksum.hexdigest(), msg)
    except Exception, exc:
        return "checksum failed: %s" % exc

