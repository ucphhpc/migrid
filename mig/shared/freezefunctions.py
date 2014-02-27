#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# --- BEGIN_HEADER ---
#
# freezefunctions - freeze archive helper functions
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

"""Freeze archive functions"""

import datetime
import os
import tempfile
import time

from shared.defaults import freeze_meta_filename
from fileio import md5sum_file, write_file, remove_rec
from shared.serial import load, dump


def build_freezeitem_object(configuration, freeze_dict):
    """Build a frozen archive object based on input freeze_dict"""

    freeze_files = []
    for file_item in freeze_dict['FILES']:
        freeze_files.append({
                'object_type': 'frozenfile',
                'name': file_item['name'],
                'size': file_item['size'],
                'md5sum': file_item['md5sum'],
                })
    return {
        'object_type': 'frozenarchive',
        'id': freeze_dict['ID'],
        'name': freeze_dict['NAME'],
        'description': freeze_dict['DESCRIPTION'],
        'creator': freeze_dict['CREATOR'],
        'created': time.asctime(freeze_dict['CREATED_TIMESTAMP'
                                ].timetuple()),
        'frozenfiles': freeze_files,
        }

def list_frozen_archives(configuration, client_id):
    """Find all frozen_archives owned by user"""
    frozen_list = []
    dir_content = []

    try:
        dir_content = os.listdir(configuration.freeze_home)
    except Exception:
        if not os.path.isdir(configuration.freeze_home):
            try:
                os.mkdir(configuration.freeze_home)
            except Exception, err:
                configuration.logger.info(
                    'refunctions.py: not able to create directory %s: %s'
                    % (configuration.freeze_home, err))

    for entry in dir_content:

        # Skip dot files/dirs

        if entry.startswith('.'):
            continue
        if is_frozen_archive(entry, configuration):

            # entry is a frozen archive - check ownership

            (meta_status, meta_out) = get_frozen_meta(entry, configuration)
            if meta_status and meta_out['CREATOR'] == client_id:
                frozen_list.append(entry)
        else:
            configuration.logger.warning(
                '%s in %s is not a directory, move it?'
                % (entry, configuration.freeze_home))
    return (True, frozen_list)

def is_frozen_archive(freeze_id, configuration):
    """Check that freeze_id is an existing frozen archive"""
    freeze_path = os.path.join(configuration.freeze_home, freeze_id)
    if os.path.isdir(freeze_path) and \
           os.path.isfile(os.path.join(freeze_path, freeze_meta_filename)):
        return True
    else:
        return False

def get_frozen_meta(freeze_id, configuration):
    """Helper to fetch dictionary of metadata for a frozen archive"""
    frozen_path = os.path.join(configuration.freeze_home, freeze_id,
                               freeze_meta_filename)
    freeze_dict = load(frozen_path)
    if not freeze_dict:
        return (False, 'Could not open metadata for frozen archive %s' % \
                freeze_id)
    else:
        return (True, freeze_dict)

def get_frozen_files(freeze_id, configuration):
    """Helper to list names and stats for files in a frozen archive"""
    frozen_dir = os.path.join(configuration.freeze_home, freeze_id)
    try:
        dir_content = os.listdir(frozen_dir)
    except Exception:
        return (False, 'Could not open frozen archive %s' % freeze_id)
    files = []
    for name in dir_content:
        if name.startswith('.') or name in [freeze_meta_filename]:
            continue
        frozen_path = os.path.join(frozen_dir, name)
        files.append({'name': name,
                      'timestamp': os.path.getctime(frozen_path),
                      'size': os.path.getsize(frozen_path),
                      # Checksum 1024 first 32k-blocks of files (i.e. 32MB)
                      'md5sum': md5sum_file(frozen_path, 32768, 1024)})
    return (True, files)

def get_frozen_archive(freeze_id, configuration):
    """Helper to extract all details for a frozen archive"""
    if not is_frozen_archive(freeze_id, configuration):
        return (False, 'no such frozen archive id: %s' % freeze_id)
    (meta_status, meta_out) = get_frozen_meta(freeze_id, configuration)
    if not meta_status:
        return (False, 'failed to extract meta data for %s' % freeze_id)
    (files_status, files_out) = get_frozen_files(freeze_id, configuration)
    if not files_status:
        return (False, 'failed to extract files for %s' % freeze_id)
    freeze_dict = {'ID': freeze_id, 'FILES': files_out}
    freeze_dict.update(meta_out)
    return (True, freeze_dict)

def create_frozen_archive(freeze_name, freeze_description, freeze_files,
                          client_id, configuration):
    """Create a new frozen archive with meta data fields and provided
    freeze_files list of name and contents.
    """
    try:
        frozen_dir = tempfile.mkdtemp(prefix='archive-',
                                       dir=configuration.freeze_home)
    except Exception, err:
        return (False, 'Error preparing new frozen archive: %s' % err)

    freeze_id = os.path.basename(frozen_dir)
    
    freeze_dict = {
        'ID': freeze_id,
        'CREATED_TIMESTAMP': datetime.datetime.now(),
        'CREATOR': client_id,
        'NAME': freeze_name,
        'DESCRIPTION': freeze_description,
        }
    configuration.logger.info("create_frozen_archive: make pickle for %s" % \
                              freeze_id)
    try:
        dump(freeze_dict, os.path.join(frozen_dir, freeze_meta_filename))
    except Exception, err:
        configuration.logger.error("create_frozen_archive: failed: %s" % \
                                   err)
        remove_rec(frozen_dir, configuration)
        return (False, 'Error writing frozen archive info: %s' % err)

    configuration.logger.info("create_frozen_archive: save %s for %s" % \
                              (freeze_files, freeze_id))
    for (filename, contents) in freeze_files:
        freeze_path = os.path.join(frozen_dir, filename)
        configuration.logger.info("create_frozen_archive: write %s" % \
                                            freeze_path)
        if not write_file(contents, freeze_path, configuration.logger):
            configuration.logger.error("create_frozen_archive: failed: %s" % \
                                       err)
            remove_rec(frozen_dir, configuration)
            return (False, 'Error writing frozen archive')
    return (True, freeze_id)

def delete_frozen_archive(freeze_id, configuration):
    """Delete an existing frozen archive without checking ownership or
    persistance of frozen archives.
    """
    frozen_dir = os.path.join(configuration.freeze_home, freeze_id)
    if remove_rec(frozen_dir, configuration):
        return (True, '')
    else:
        return (False, 'Error deleting frozen archive "%s"' % freeze_id)

