#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# --- BEGIN_HEADER ---
#
# vgridaccess - user access in VGrids
# Copyright (C) 2003-2020  The MiG Project lead by Brian Vinter
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

"""User access to VGrids"""

import copy
import os
import time
import fcntl

from shared.base import sandbox_resource, client_id_dir
from shared.conf import get_all_exe_vgrids, get_all_store_vgrids, \
    get_resource_fields, get_resource_configuration
from shared.defaults import settings_filename, profile_filename, default_vgrid
from shared.modified import mark_resource_modified, mark_vgrid_modified, \
    check_users_modified, check_resources_modified, check_vgrids_modified, \
    reset_users_modified, reset_resources_modified, reset_vgrids_modified
from shared.resource import list_resources, real_to_anon_res_map
from shared.serial import load, dump
from shared.user import list_users, real_to_anon_user_map, get_user_conf
from shared.vgrid import vgrid_list_vgrids, vgrid_allowed, vgrid_resources, \
    user_allowed_vgrids, vgrid_owners, vgrid_members, vgrid_settings, \
    vgrid_list_subvgrids, vgrid_list_parents, res_allowed_vgrids, \
    merge_vgrid_settings

MAP_SECTIONS = (USERS, RESOURCES, VGRIDS) = ("__users__", "__resources__",
                                             "__vgrids__")
RES_SPECIALS = (ALLOW, ALLOWEXE, ALLOWSTORE, ASSIGN, ASSIGNEXE, ASSIGNSTORE,
                USERID, RESID, OWNERS, MEMBERS, CONF, MODTIME, EXEVGRIDS,
                STOREVGRIDS) = \
    ('__allow__', '__allowexe__', '__allowstore__', '__assign__',
     '__assignexe__', '__assignstore__', '__userid__', '__resid__',
     '__owners__', '__members__', '__conf__', '__modtime__',
     '__exevgrids__', '__storevgrids__')
# VGrid-specific settings
SETTINGS = '__settings__'

# Never repeatedly refresh maps within this number of seconds in same process
# Used to avoid refresh floods with e.g. runtime envs page calling
# refresh for each env when extracting providers.
MAP_CACHE_SECONDS = 60

last_refresh = {USERS: 0, RESOURCES: 0, VGRIDS: 0}
last_load = {USERS: 0, RESOURCES: 0, VGRIDS: 0}
last_map = {USERS: {}, RESOURCES: {}, VGRIDS: {}}


def load_entity_map(configuration, kind, do_lock):
    """Load map of given entities and their configuration. Uses a pickled
    dictionary for efficiency. The do_lock option is used to enable and
    disable locking during load.
    Entity IDs are stored in their raw (non-anonymized form).
    Returns tuple with map and time stamp of last map modification.
    Please note that time stamp is explicitly set to start of last update
    to make sure any concurrent updates get caught in next run.
    """
    _logger = configuration.logger
    map_path = os.path.join(configuration.mig_system_files, "%s.map" % kind)
    lock_path = os.path.join(configuration.mig_system_files, "%s.lock" % kind)
    if do_lock:
        lock_handle = open(lock_path, 'a')
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_SH)
    try:
        _logger.info("before %s map load" % kind)
        entity_map = load(map_path)
        _logger.info("after %s map load" % kind)
        map_stamp = os.path.getmtime(map_path)
    except IOError:
        _logger.warn("No %s map to load" % kind)
        entity_map = {}
        map_stamp = -1
    if do_lock:
        lock_handle.close()
    return (entity_map, map_stamp)


def load_user_map(configuration, do_lock=True):
    """Load map of users and their configuration. Uses a pickled
    dictionary for efficiency. Optional do_lock option is used to enable and
    disable locking during load.
    User IDs are stored in their raw (non-anonymized form).
    Returns tuple with map and time stamp of last map modification.
    """
    return load_entity_map(configuration, 'user', do_lock)


def load_resource_map(configuration, do_lock=True):
    """Load map of resources and their configuration. Uses a pickled
    dictionary for efficiency. Optional do_lock option is used to enable and
    disable locking during load.
    Resource IDs are stored in their raw (non-anonymized form).
    """
    return load_entity_map(configuration, 'resource', do_lock)


def load_vgrid_map(configuration, do_lock=True):
    """Load map of vgrids and their configuration. Uses a pickled
    dictionary for efficiency. Optional do_lock option is used to enable and
    disable locking during load.
    Resource IDs are stored in their raw (non-anonymized form).
    """
    return load_entity_map(configuration, 'vgrid', do_lock)


def refresh_user_map(configuration, clean=False):
    """Refresh map of users and their configuration. Uses a pickled
    dictionary for efficiency.
    User IDs are stored in their raw (non-anonymized form).
    Only update map for users that updated conf after last map save.
    NOTE: Save start time so that any concurrent updates get caught next time.
    """
    _logger = configuration.logger
    start_time = time.time()
    dirty = []
    map_path = os.path.join(configuration.mig_system_files, "user.map")
    lock_path = os.path.join(configuration.mig_system_files, "user.lock")
    lock_handle = open(lock_path, 'a')
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    if not clean:
        user_map, map_stamp = load_user_map(configuration, do_lock=False)
    else:
        _logger.info("Creating clean user map")
        user_map = {}
        map_stamp = 0

    # Find all users and their configurations

    all_users = list_users(configuration)
    real_map = real_to_anon_user_map(configuration)
    for user in all_users:
        settings_path = os.path.join(configuration.user_settings,
                                     client_id_dir(user), settings_filename)
        profile_path = os.path.join(configuration.user_settings,
                                    client_id_dir(user), profile_filename)
        settings_mtime, profile_mtime = 0, 0
        if os.path.isfile(settings_path):
            settings_mtime = os.path.getmtime(settings_path)
        if os.path.isfile(profile_path):
            profile_mtime = os.path.getmtime(profile_path)

        if settings_mtime + profile_mtime > 0:
            conf_mtime = max(settings_mtime, profile_mtime)
        else:
            conf_mtime = -1
        # init first time
        user_map[user] = user_map.get(user, {})
        if not user_map[user].has_key(CONF) or conf_mtime >= map_stamp:
            user_conf = get_user_conf(user, configuration, True)
            if not user_conf:
                user_conf = {}
            user_map[user][CONF] = user_conf
            public_id = user
            if user_conf.get('ANONYMOUS', True):
                public_id = real_map[user]
            user_map[user][USERID] = public_id
            user_map[user][MODTIME] = map_stamp
            dirty += [user]
    # Remove any missing users from map
    missing_user = [user for user in user_map.keys()
                    if not user in all_users]
    for user in missing_user:
        del user_map[user]
        dirty += [user]

    if dirty:
        try:
            dump(user_map, map_path)
            os.utime(map_path, (start_time, start_time))
        except Exception, exc:
            _logger.error("Could not save user map: %s" % exc)

    last_refresh[USERS] = start_time
    lock_handle.close()

    return user_map


def refresh_resource_map(configuration, clean=False):
    """Refresh map of resources and their configuration. Uses a pickled
    dictionary for efficiency.
    Resource IDs are stored in their raw (non-anonymized form).
    Only update map for resources that updated conf after last map save.
    NOTE: Save start time so that any concurrent updates get caught next time.
    """
    _logger = configuration.logger
    start_time = time.time()
    dirty = []
    map_path = os.path.join(configuration.mig_system_files, "resource.map")
    lock_path = os.path.join(configuration.mig_system_files, "resource.lock")
    lock_handle = open(lock_path, 'a')
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    if not clean:
        resource_map, map_stamp = load_resource_map(
            configuration, do_lock=False)
    else:
        _logger.info("Creating clean resource map")
        resource_map = {}
        map_stamp = 0

    # Find all resources and their configurations

    all_resources = list_resources(configuration.resource_home,
                                   only_valid=True)
    real_map = real_to_anon_res_map(configuration.resource_home)
    for res in all_resources:
        # Sandboxes do not change their configuration
        if resource_map.has_key(res) and sandbox_resource(res):
            continue
        conf_path = os.path.join(configuration.resource_home, res, "config")
        if not os.path.isfile(conf_path):
            continue
        conf_mtime = os.path.getmtime(conf_path)
        owners_path = os.path.join(configuration.resource_home, res, "owners")
        if not os.path.isfile(owners_path):
            continue
        owners_mtime = os.path.getmtime(owners_path)
        # init first time
        resource_map[res] = resource_map.get(res, {})
        if not resource_map[res].has_key(CONF) or conf_mtime >= map_stamp:
            (status, res_conf) = get_resource_configuration(
                configuration.resource_home, res, configuration.logger)
            if not status:
                _logger.warning(
                    "could not load conf for %s" % res)
                continue
            resource_map[res][CONF] = res_conf
            public_id = res
            if res_conf.get('ANONYMOUS', True):
                public_id = real_map[res]
            resource_map[res][RESID] = public_id
            resource_map[res][MODTIME] = map_stamp
            dirty += [res]
        if not resource_map[res].has_key(OWNERS) or owners_mtime >= map_stamp:
            owners = load(owners_path)
            resource_map[res][OWNERS] = owners
            resource_map[res][MODTIME] = map_stamp
            dirty += [res]
    # Remove any missing resources from map
    missing_res = [res for res in resource_map.keys()
                   if not res in all_resources]
    for res in missing_res:
        del resource_map[res]
        dirty += [res]

    if dirty:
        try:
            dump(resource_map, map_path)
            os.utime(map_path, (start_time, start_time))
        except Exception, exc:
            _logger.error("Could not save resource map: %s" % exc)

    last_refresh[RESOURCES] = start_time
    lock_handle.close()

    return resource_map


def refresh_vgrid_map(configuration, clean=False):
    """Refresh map of users and resources with their direct vgrid
    participation. That is, without inheritance. Uses a pickled dictionary for
    efficiency.
    Resource and user IDs are stored in their raw (non-anonymized form).
    Only update map for users and resources that updated conf after last map
    save.
    NOTE: Save start time so that any concurrent updates get caught next time.
    """
    _logger = configuration.logger
    start_time = time.time()
    dirty = {}
    vgrid_changes = {}
    map_path = os.path.join(configuration.mig_system_files, "vgrid.map")
    lock_path = os.path.join(configuration.mig_system_files, "vgrid.lock")
    lock_handle = open(lock_path, 'a')
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    if not clean:
        vgrid_map, map_stamp = load_vgrid_map(configuration, do_lock=False)
    else:
        _logger.info("Creating clean vgrid map")
        vgrid_map = {}
        map_stamp = 0

    vgrid_helper = {default_vgrid: {RESOURCES: ['*'],
                                    OWNERS: [], MEMBERS: ['*'],
                                    SETTINGS: []}}
    if not vgrid_map.has_key(VGRIDS):
        vgrid_map[VGRIDS] = vgrid_helper
        dirty[VGRIDS] = dirty.get(VGRIDS, []) + [default_vgrid]
    if not vgrid_map.has_key(RESOURCES):
        vgrid_map[RESOURCES] = {}
        dirty[RESOURCES] = dirty.get(RESOURCES, [])
    if not vgrid_map.has_key(USERS):
        vgrid_map[USERS] = {}
        dirty[USERS] = dirty.get(USERS, [])

    # Find all vgrids and their allowed users and resources - from disk

    (status, all_vgrids) = vgrid_list_vgrids(configuration)
    if not status:
        all_vgrids = []

    conf_read = [(RESOURCES, configuration.vgrid_resources, vgrid_resources),
                 (OWNERS, configuration.vgrid_owners, vgrid_owners),
                 (MEMBERS, configuration.vgrid_members, vgrid_members),
                 (SETTINGS, configuration.vgrid_settings, vgrid_settings)]
    optional_conf = [SETTINGS, ]

    for vgrid in all_vgrids:
        for (field, name, list_call) in conf_read:
            conf_path = os.path.join(configuration.vgrid_home, vgrid, name)
            if not os.path.isfile(conf_path):
                # Make sure vgrid dict exists before filling it
                vgrid_map[VGRIDS][vgrid] = vgrid_map[VGRIDS].get(vgrid, {})
                vgrid_map[VGRIDS][vgrid][field] = []
                if vgrid != default_vgrid and field not in optional_conf:
                    _logger.warning('missing file: %s' %
                                    conf_path)
                    dirty[VGRIDS] = dirty.get(VGRIDS, []) + [vgrid]

            elif not vgrid_map[VGRIDS].has_key(vgrid) or \
                    os.path.getmtime(conf_path) >= map_stamp:
                (status, entries) = list_call(vgrid, configuration,
                                              recursive=False)
                if not status:
                    entries = []
                vgrid_changes[vgrid] = vgrid_changes.get(vgrid, {})
                map_entry = vgrid_map[VGRIDS].get(vgrid, {})
                vgrid_changes[vgrid][field] = (map_entry.get(field, []),
                                               entries)
                vgrid_map[VGRIDS][vgrid] = map_entry
                vgrid_map[VGRIDS][vgrid][field] = entries
                dirty[VGRIDS] = dirty.get(VGRIDS, []) + [vgrid]
    # Remove any missing vgrids from map
    missing_vgrids = [vgrid for vgrid in vgrid_map[VGRIDS].keys()
                      if not vgrid in all_vgrids]
    for vgrid in missing_vgrids:
        vgrid_changes[vgrid] = vgrid_changes.get(vgrid, {})
        map_entry = vgrid_map[VGRIDS].get(vgrid, {})
        for (field, _, _) in conf_read:
            vgrid_changes[vgrid][field] = (map_entry.get(field, []), [])
        del vgrid_map[VGRIDS][vgrid]
        dirty[VGRIDS] = dirty.get(VGRIDS, []) + [vgrid]

    # Find all resources and their vgrid assignments

    # TODO: use get_resource_map output instead?
    all_resources = list_resources(
        configuration.resource_home, only_valid=True)
    real_map = real_to_anon_res_map(configuration.resource_home)
    for res in all_resources:
        # Sandboxes do not change their vgrid participation
        if vgrid_map[RESOURCES].has_key(res) and sandbox_resource(res):
            continue
        conf_path = os.path.join(configuration.resource_home, res, "config")
        if not os.path.isfile(conf_path):
            continue
        if os.path.getmtime(conf_path) >= map_stamp:
            # Read maps of exe name to vgrid list and of store name to vgrid
            # list. Save them separately to be able to distinguish them in
            # exe / store access and visibility
            store_vgrids = get_all_store_vgrids(res)
            exe_vgrids = get_all_exe_vgrids(res)
            # Preserve top level exes for backward compatibility until we have
            # switched to new EXEVGRIDS and STOREVGRIDS sub dicts everywhere.
            # NOTE: we copy exe_vgrids values here to avoid polluting it below!
            vgrid_map[RESOURCES][res] = {}
            vgrid_map[RESOURCES][res].update(exe_vgrids)
            vgrid_map[RESOURCES][res][EXEVGRIDS] = exe_vgrids
            vgrid_map[RESOURCES][res][STOREVGRIDS] = store_vgrids
            assignexe, assignstore = [], []
            for (res_unit, unit_vgrids) in exe_vgrids.items():
                assignexe += [i for i in unit_vgrids
                              if i and i not in assignexe]
            for (res_unit, unit_vgrids) in store_vgrids.items():
                assignstore += [i for i in unit_vgrids
                                if i and i not in assignstore]
            # Preserve these two unspecific legacy fields for now
            vgrid_map[RESOURCES][res][ASSIGN] = assignexe
            vgrid_map[RESOURCES][res][ALLOW] = \
                vgrid_map[RESOURCES][res].get(ALLOW, [])
            vgrid_map[RESOURCES][res][ASSIGNEXE] = assignexe
            vgrid_map[RESOURCES][res][ASSIGNSTORE] = assignstore
            vgrid_map[RESOURCES][res][ALLOWEXE] = \
                vgrid_map[RESOURCES][res].get(ALLOWEXE, [])
            vgrid_map[RESOURCES][res][ALLOWSTORE] = \
                vgrid_map[RESOURCES][res].get(ALLOWSTORE, [])
            public_id = res
            anon_val = get_resource_fields(configuration.resource_home, res,
                                           ['ANONYMOUS'], configuration.logger)
            if anon_val.get('ANONYMOUS', True):
                public_id = real_map[res]
            vgrid_map[RESOURCES][res][RESID] = public_id
            dirty[RESOURCES] = dirty.get(RESOURCES, []) + [res]
    # Remove any missing resources from map
    missing_res = [res for res in vgrid_map[RESOURCES].keys()
                   if not res in all_resources]
    for res in missing_res:
        del vgrid_map[RESOURCES][res]
        dirty[RESOURCES] = dirty.get(RESOURCES, []) + [res]

    # Update list of mutually agreed vgrid participations for dirty resources
    # and resources assigned to dirty vgrids
    _logger.info(
        "update res vgrid participations: %s" % vgrid_changes)
    update_res = [i for i in dirty.get(RESOURCES, []) if i not in MAP_SECTIONS]
    # _logger.info("update vgrid allow res")
    for (vgrid, changes) in vgrid_changes.items():
        old, new = changes.get(RESOURCES, ([], []))
        if old == new:
            _logger.debug("skip res update of vgrid %s (%s)" %
                          (vgrid, changes))
            continue
        # _logger.info("update res vgrid %s" % vgrid)
        for res in [i for i in vgrid_map[RESOURCES].keys()
                    if i not in update_res]:
            # Sandboxes do not change their vgrid participation
            if sandbox_resource(res):
                continue
            # _logger.info("update res vgrid %s for res %s" % (vgrid, res))
            if vgrid_allowed(res, old) != vgrid_allowed(res, new):
                update_res.append(res)
    # _logger.info("update res assign vgrid")
    for res in [i for i in update_res if i not in missing_res]:
        allowexe, allowstore = [], []
        res_data = vgrid_map[RESOURCES][res]
        # Gracefully update any legacy values
        res_data[ALLOWEXE] = res_data.get(ALLOWEXE, res_data[ALLOW])
        res_data[ALLOWSTORE] = res_data.get(ALLOWSTORE, [])
        res_data[ASSIGNEXE] = res_data.get(ASSIGNEXE, res_data[ASSIGN])
        res_data[ASSIGNSTORE] = res_data.get(ASSIGNSTORE, [])
        assignexe = res_data[ASSIGNEXE]
        assignstore = res_data[ASSIGNSTORE]
        for vgrid in assignexe:
            if vgrid_allowed(res, vgrid_map[VGRIDS][vgrid][RESOURCES]):
                allowexe.append(vgrid)
            # Preserve legacy field for now
            vgrid_map[RESOURCES][res][ALLOW] = allowexe
            vgrid_map[RESOURCES][res][ALLOWEXE] = allowexe
        for vgrid in assignstore:
            if vgrid_allowed(res, vgrid_map[VGRIDS][vgrid][RESOURCES]):
                allowstore.append(vgrid)
            vgrid_map[RESOURCES][res][ALLOWSTORE] = allowstore

    _logger.info("done updating vgrid res participations")

    # Find all users and their vgrid assignments

    # TODO: use get_user_map output instead?
    all_users = list_users(configuration)
    real_map = real_to_anon_user_map(configuration)
    for user in all_users:
        settings_path = os.path.join(configuration.user_settings,
                                     client_id_dir(user), settings_filename)
        profile_path = os.path.join(configuration.user_settings,
                                    client_id_dir(user), profile_filename)
        settings_mtime, profile_mtime = 0, 0
        if os.path.isfile(settings_path):
            settings_mtime = os.path.getmtime(settings_path)
        if os.path.isfile(profile_path):
            profile_mtime = os.path.getmtime(profile_path)

        if settings_mtime + profile_mtime > 0:
            conf_mtime = max(settings_mtime, profile_mtime)
            user_conf = get_user_conf(user, configuration)
        else:
            conf_mtime = -1
            user_conf = {}
        if conf_mtime >= map_stamp:
            vgrid_map[USERS][user] = user_conf
            vgrid_map[USERS][user][ASSIGN] = vgrid_map[USERS][user].get(ASSIGN,
                                                                        [])
            vgrid_map[USERS][user][ALLOW] = vgrid_map[USERS][user].get(ALLOW,
                                                                       [])
            public_id = user
            if user_conf.get('ANONYMOUS', True):
                public_id = real_map[user]
            vgrid_map[USERS][user][USERID] = public_id
            dirty[USERS] = dirty.get(USERS, []) + [user]
    # Remove any missing users from map
    missing_user = [user for user in vgrid_map[USERS].keys()
                    if not user in all_users]
    for user in missing_user:
        del vgrid_map[USERS][user]
        dirty[USERS] = dirty.get(USERS, []) + [user]

    # Update list of mutually agreed vgrid participations for dirty users
    # and users assigned to dirty vgrids
    update_user = [i for i in dirty.get(USERS, []) if i not in MAP_SECTIONS]
    for (vgrid, changes) in vgrid_changes.items():
        old_owners, new_owners = changes.get(OWNERS, ([], []))
        old_members, new_members = changes.get(MEMBERS, ([], []))
        if old_owners == new_owners and old_members == new_members:
            _logger.debug("skip user update of vgrid %s (%s)" %
                          (vgrid, changes))
            continue
        (old, new) = (old_owners + old_members, new_owners + new_members)
        for user in [i for i in vgrid_map[USERS].keys()
                     if i not in update_user]:
            if vgrid_allowed(user, old) != vgrid_allowed(user, new):
                _logger.info("update user vgrid %s for user %s" %
                             (vgrid, user))
                update_user.append(user)
    for user in [i for i in update_user if i not in missing_user]:
        allow = []
        for vgrid in vgrid_map[USERS][user][ASSIGN]:
            if vgrid_allowed(user, vgrid_map[VGRIDS][vgrid][OWNERS]) or \
                    vgrid_allowed(user, vgrid_map[VGRIDS][vgrid][MEMBERS]):
                allow.append(vgrid)
            # users implicitly assign all vgrids
            vgrid_map[USERS][user][ASSIGN] = allow
            vgrid_map[USERS][user][ALLOW] = allow

    if dirty:
        _logger.info("Saving vgrid map changes: %s" % dirty)
        try:
            dump(vgrid_map, map_path)
            os.utime(map_path, (start_time, start_time))
        except Exception, exc:
            _logger.error("Could not save vgrid map: %s" % exc)

    last_refresh[VGRIDS] = start_time
    lock_handle.close()

    return vgrid_map


def force_update_user_map(configuration, clean=False):
    """Refresh user map and update map cache"""
    map_stamp = load_stamp = time.time()
    user_map = refresh_user_map(configuration, clean=clean)
    last_map[USERS] = user_map
    last_refresh[USERS] = map_stamp
    last_load[USERS] = load_stamp

    return user_map


def force_update_resource_map(configuration, clean=False):
    """Refresh resources map and update map cache"""
    map_stamp = load_stamp = time.time()
    resource_map = refresh_resource_map(configuration, clean=clean)
    last_map[RESOURCES] = resource_map
    last_refresh[RESOURCES] = map_stamp
    last_load[RESOURCES] = load_stamp

    return resource_map


def force_update_vgrid_map(configuration, clean=False):
    """Refresh vgrid map and update map cache"""
    map_stamp = load_stamp = time.time()
    vgrid_map = refresh_vgrid_map(configuration, clean=clean)
    last_map[VGRIDS] = vgrid_map
    last_refresh[VGRIDS] = map_stamp
    last_load[VGRIDS] = load_stamp

    return vgrid_map


def get_user_map(configuration, caching=False):
    """Returns the current map of users and their configurations. Caches the
    map for load prevention with repeated calls within short time span.
    """
    _logger = configuration.logger
    if last_load[USERS] + MAP_CACHE_SECONDS > time.time():
        _logger.debug("using cached user map")
        return last_map[USERS]
    modified_users, _ = check_users_modified(configuration)
    if modified_users and not caching:
        _logger.info("refreshing user map (%s)" % modified_users)
        map_stamp = load_stamp = time.time()
        user_map = refresh_user_map(configuration)
        reset_users_modified(configuration)
    else:
        _logger.debug("No changes or forced caching - not refreshing")
        load_stamp = time.time()
        user_map, map_stamp = load_user_map(configuration)
    last_map[USERS] = user_map
    last_refresh[USERS] = map_stamp
    last_load[USERS] = load_stamp
    return user_map


def get_resource_map(configuration, caching=False):
    """Returns the current map of resources and their configurations.
    Caches the map for load prevention with repeated calls
    within short time span.
    """
    _logger = configuration.logger
    if last_load[RESOURCES] + MAP_CACHE_SECONDS > time.time():
        _logger.debug("using cached resource map")
        return last_map[RESOURCES]
    modified_resources, _ = check_resources_modified(configuration)
    if modified_resources and not caching:
        _logger.info(
            "refreshing resource map (%s)" % modified_resources)
        map_stamp = load_stamp = time.time()
        resource_map = refresh_resource_map(configuration)
        reset_resources_modified(configuration)
    else:
        _logger.debug("No changes or forced caching- not refreshing")
        load_stamp = time.time()
        resource_map, map_stamp = load_resource_map(configuration)
    last_map[RESOURCES] = resource_map
    last_refresh[RESOURCES] = map_stamp
    last_load[RESOURCES] = load_stamp
    return resource_map


def vgrid_inherit_map(configuration, vgrid_map):
    """Takes a vgrid_map and returns a copy extended with inherited values.
    That is, if the vgrid_map has vgrid A with owner John Doe all sub-vgrids
    A/B, A/B/C, A/M, etc. get their owner list set to include John Doe as well.
    """
    inherit_map = copy.deepcopy(vgrid_map)
    # Sort vgrids and extend participation from the end to keep it simple
    # and efficient
    all_vgrids = inherit_map[VGRIDS].keys()
    all_vgrids.sort()
    for vgrid_name in all_vgrids[::-1]:
        vgrid = inherit_map[VGRIDS][vgrid_name]
        # Get parent vgrids in root-to-leaf order
        parent_vgrid_list = vgrid_list_parents(vgrid_name, configuration)
        # Build a list of dicts to merge and then force back to tuples
        settings_list = []
        for parent_name in parent_vgrid_list:
            parent_vgrid = inherit_map[VGRIDS][parent_name]
            for field in (OWNERS, MEMBERS, RESOURCES):
                vgrid[field] += [i for i in parent_vgrid[field] if not i in
                                 vgrid[field]]
            settings_list.append(dict(parent_vgrid.get(SETTINGS, [])))
        settings_list.append(dict(vgrid.get(SETTINGS, [])))
        for field in (SETTINGS, ):
            merged = merge_vgrid_settings(vgrid_name, configuration,
                                          settings_list)
            # Force back to tuple form for symmetry with non-inherit version
            vgrid[field] = merged.items()
    return inherit_map


def get_vgrid_map(configuration, recursive=True, caching=False):
    """Returns the current map of vgrids and their configurations. Caches the
    map for load prevention with repeated calls within short time span.
    The recursive parameter is there to request extension of all sub-vgrids
    participation with inherited entities. The raw vgrid map only mirrors the
    direct participation.
    """
    _logger = configuration.logger
    if last_load[VGRIDS] + MAP_CACHE_SECONDS > time.time():
        _logger.debug("using cached vgrid map")
        vgrid_map = last_map[VGRIDS]
    else:
        modified_vgrids, _ = check_vgrids_modified(configuration)
        if modified_vgrids and not caching:
            _logger.info("refreshing vgrid map (%s)" %
                         modified_vgrids)
            map_stamp = load_stamp = time.time()
            vgrid_map = refresh_vgrid_map(configuration)
            reset_vgrids_modified(configuration)
            _logger.info("refreshed vgrid map (%s)" %
                         modified_vgrids)
        else:
            _logger.debug("No changes or forced caching - not refreshing")
            load_stamp = time.time()
            vgrid_map, map_stamp = load_vgrid_map(configuration)
        last_map[VGRIDS] = vgrid_map
        last_refresh[VGRIDS] = map_stamp
        last_load[VGRIDS] = load_stamp
    if recursive:
        return vgrid_inherit_map(configuration, vgrid_map)
    else:
        return vgrid_map


def get_vgrid_map_vgrids(configuration, recursive=True, sort=True,
                         caching=False):
    """Returns the current list of vgrids from vgrid map. Caches the
    map for load prevention with repeated calls within short time span.
    The recursive parameter is there to request extension of all sub-vgrids
    participation with inherited entities.
    """
    vgrid_map = get_vgrid_map(configuration, recursive, caching)
    vgrid_list = vgrid_map.get(VGRIDS, {}).keys()
    if sort:
        vgrid_list.sort()
    return vgrid_list


def user_vgrid_access(configuration, client_id, inherited=False,
                      recursive=True, caching=False):
    """Extract a list of vgrids that user is allowed to access either due to
    owner or membership. The optional inherited argument tells the function to
    expand vgrid access to *parent* vgrids so that the somewhat broken reverse
    inheritance for jobs to access resources on parent vgrids can be applied.
    The optional recursive argument is passed directly to the get_vgrid_map
    call so please refer to the use there.
    Thus this is basically the fast equivalent of the user_allowed_vgrids from
    the vgrid module and should replace that one everywhere that only vgrid map
    (cached) lookups are needed.
    """
    vgrid_access = [default_vgrid]
    vgrid_map = get_vgrid_map(configuration, recursive, caching)
    for vgrid in vgrid_map[VGRIDS].keys():
        if vgrid_allowed(client_id, vgrid_map[VGRIDS][vgrid][OWNERS]) or \
                vgrid_allowed(client_id, vgrid_map[VGRIDS][vgrid][MEMBERS]):
            if inherited:
                vgrid_access += vgrid_list_parents(vgrid, configuration)
            vgrid_access.append(vgrid)
    return vgrid_access


def check_vgrid_access(configuration, client_id, vgrid_name, recursive=True,
                       caching=False):
    """Inspect the vgrid map and check if client_id is either a member or
    owner of vgrid_name.
    The optional recursive argument is passed directly to the get_vgrid_map
    call so please refer to the use there.
    Thus this is basically the fast equivalent of vgrid_is_owner_or_member from
    the vgrid module and should replace that one everywhere that only vgrid map
    (cached) lookups are needed.
    """
    vgrid_access = [default_vgrid]
    vgrid_map = get_vgrid_map(configuration, recursive, caching)
    vgrid_entry = vgrid_map[VGRIDS].get(vgrid_name, {OWNERS: [], MEMBERS: []})
    return vgrid_allowed(client_id, vgrid_entry[OWNERS]) or \
        vgrid_allowed(client_id, vgrid_entry[MEMBERS])


def res_vgrid_access(configuration, client_id, recursive=True, caching=False):
    """Extract a list of vgrids that resource is allowed to access.
    The optional recursive argument is passed directly to the get_vgrid_map
    call so please refer to the use there.
    Thus this is basically the fast equivalent of the res_allowed_vgrids from
    the vgrid module and should replace that one everywhere that only vgrid map
    (cached) lookups are needed.
    """
    vgrid_access = [default_vgrid]
    vgrid_map = get_vgrid_map(configuration, recursive, caching)
    for vgrid in vgrid_map[VGRIDS].keys():
        if vgrid_allowed(client_id, vgrid_map[VGRIDS][vgrid][RESOURCES]):
            vgrid_access.append(vgrid)
    return vgrid_access


def user_owned_res_confs(configuration, client_id, caching=False):
    """Extract a map of resources that client_id owns.

    Returns a map from resource IDs to resource conf dictionaries.

    Resource IDs are anonymized unless explicitly configured otherwise, but
    the resource confs are always raw.
    """
    owned = {}
    resource_map = get_resource_map(configuration, caching)

    # Map only contains the raw resource names - anonymize as requested

    anon_map = {}
    for res in resource_map.keys():
        anon_map[res] = resource_map[res][RESID]

    for (res_id, res) in resource_map.items():
        if vgrid_allowed(client_id, res[OWNERS]):
            owned[anon_map[res_id]] = res[CONF]
    return owned


def user_allowed_res_confs(configuration, client_id, caching=False):
    """Extract a map of resources that client_id can really submit to or store
    data on.
    There is no guarantee that they will ever be online to accept any further
    jobs or host data.

    Returns a map from resource IDs to resource conf dictionaries.

    Resources are anonymized unless explicitly configured otherwise, but
    the resource confs are always raw.

    Please note that vgrid participation is a mutual agreement between vgrid
    owners and resource owners, so that a resource only truly participates
    in a vgrid if the vgrid *and* resource owners configured it so.
    """
    allowed = {}

    # Extend allowed_vgrids with any parent vgrids here to fit inheritance

    allowed_vgrids = user_vgrid_access(configuration, client_id,
                                       inherited=True, caching=caching)

    # Find all potential resources from vgrid sign up

    vgrid_map = get_vgrid_map(configuration, caching=caching)
    vgrid_map_res = vgrid_map[RESOURCES]
    resource_map = get_resource_map(configuration)

    # Map only contains the raw resource names - anonymize as requested

    anon_map = {}
    for res in vgrid_map_res.keys():
        anon_map[res] = vgrid_map_res[res][RESID]

    # Now select only the ones that actually are assigned to a shared vgrid.
    # TODO: should we prefilter to ALLOWEXE+ALLOWSTORE+[default_vgrid]?
    #       like we do in user_allowed_res_units

    for (res, res_data) in vgrid_map_res.items():
        # Gracefully update any legacy values
        res_data[ASSIGNEXE] = res_data.get(ASSIGNEXE, res_data[ASSIGN])
        res_data[ASSIGNSTORE] = res_data.get(ASSIGNSTORE, [])
        assignexe = res_data[ASSIGNEXE]
        assignstore = res_data[ASSIGNSTORE]
        shared = [i for i in assignexe + assignstore if i in allowed_vgrids]
        if not shared:
            continue
        allowed[anon_map[res]] = resource_map.get(res, {CONF: {}})[CONF]
    return allowed


def user_visible_res_confs(configuration, client_id, caching=False):
    """Extract a map of resources that client_id owns or can submit jobs to.
    This is a wrapper combining user_owned_res_confs and
    user_allowed_res_confs.

    Returns a map from resource IDs to resource conf dictionaries.

    Resource IDs are anonymized unless explicitly configured otherwise, but
    the resource confs are always raw.
    """
    visible = user_allowed_res_confs(configuration, client_id, caching)
    visible.update(user_owned_res_confs(configuration, client_id, caching))
    return visible


def user_owned_res_exes(configuration, client_id, caching=False):
    """Extract a map of resource exes that client_id owns.

    Returns a map from resource IDs to lists of exe node names.

    Resource IDs are anonymized unless explicitly configured otherwise.
    """
    owned = {}
    owned_confs = user_owned_res_confs(configuration, client_id, caching)
    for (res_id, res) in owned_confs.items():
        # NOTE: we need to allow missing EXECONFIG
        owned[res_id] = [exe["name"] for exe in res.get("EXECONFIG", [])]
    return owned


def user_owned_res_stores(configuration, client_id, caching=False):
    """Extract a map of resources that client_id owns.

    Returns a map from resource IDs to lists of store node names.

    Resource IDs are anonymized unless explicitly configured otherwise.
    """
    owned = {}
    owned_confs = user_owned_res_confs(configuration, client_id, caching)
    for (res_id, res) in owned_confs.items():
        # NOTE: we need to allow missing STORECONFIG
        owned[res_id] = [store["name"] for store in res.get("STORECONFIG", [])]
    return owned


def user_allowed_res_units(configuration, client_id, unit_type, caching=False):
    """Find resource units of unit_type exe or store that client_id is allowed
    to use.
    """
    _logger = configuration.logger
    allowed = {}

    # Extend allowed_vgrids with any parent vgrids here to fit inheritance

    allowed_vgrids = user_vgrid_access(configuration, client_id,
                                       inherited=True, caching=caching)

    # Find all potential resources from vgrid sign up

    vgrid_map = get_vgrid_map(configuration, caching=caching)
    vgrid_map_res = vgrid_map[RESOURCES]

    # Map only contains the raw resource names - anonymize as requested

    anon_map = {}
    for res in vgrid_map_res.keys():
        anon_map[res] = vgrid_map_res[res][RESID]

    # Now select only the ones that actually still are allowed for that vgrid

    for (res, res_data) in vgrid_map_res.items():
        # Gracefully update any legacy values
        res_data[EXEVGRIDS] = res_data.get(EXEVGRIDS,
                                           dict([(i, j) for (i, j) in
                                                 res_data.items() if i not in
                                                 RES_SPECIALS]))
        res_data[STOREVGRIDS] = res_data.get(STOREVGRIDS, {})
        res_data[ALLOWEXE] = res_data.get(ALLOWEXE, res_data[ALLOW])
        res_data[ALLOWSTORE] = res_data.get(ALLOWSTORE, [])
        if unit_type == "exe":
            allowunit = res_data[ALLOWEXE]
            assignvgrid = res_data[EXEVGRIDS]
        elif unit_type == "store":
            allowunit = res_data[ALLOWSTORE]
            assignvgrid = res_data[STOREVGRIDS]
        else:
            _logger.error("unexpected unit_type: %s" % unit_type)
            return allowed
        # We add the implicit default_vgrid here as it is not in allowunit.
        shared = [i for i in allowunit +
                  [default_vgrid] if i in allowed_vgrids]
        # Please note that that shared will always include default_vgrid. We
        # additionally filter on actual assignment to avoid global access.
        match = []
        for (res_unit, unit_vgrids) in assignvgrid.items():
            if [i for i in shared if i in unit_vgrids]:
                match.append(res_unit)
        if match:
            allowed[anon_map[res]] = match
    return allowed


def user_allowed_res_exes(configuration, client_id, caching=False):
    """Extract a map of resources that client_id can really submit to.
    There is no guarantee that they will ever accept any further jobs.

    Returns a map from resource IDs to lists of exe node names.

    Resource IDs are anonymized unless explicitly configured otherwise.

    Please note that vgrid participation is a mutual agreement between vgrid
    owners and resource owners, so that a resource only truly participates
    in a vgrid if the vgrid *and* resource owners configured it so.
    """
    return user_allowed_res_units(configuration, client_id, "exe", caching)


def user_allowed_res_stores(configuration, client_id, caching=False):
    """Extract a map of resources that client_id can really store data on.
    There is no guarantee that they will ever be available for storing again.

    Returns a map from resource IDs to lists of store node names.

    Resource IDs are anonymized unless explicitly configured otherwise.

    Please note that vgrid participation is a mutual agreement between vgrid
    owners and resource owners, so that a resource only truly participates
    in a vgrid if the vgrid *and* resource owners configured it so.
    """
    return user_allowed_res_units(configuration, client_id, "store", caching)


def user_visible_res_exes(configuration, client_id, caching=False):
    """Extract a map of resources that client_id owns or can submit jobs to.
    This is a wrapper combining user_owned_res_exes and
    user_allowed_res_exes.

    Returns a map from resource IDs to resource exe node names.

    Resource IDs are anonymized unless explicitly configured otherwise.
    """
    visible = user_allowed_res_exes(configuration, client_id, caching)
    visible.update(user_owned_res_exes(configuration, client_id, caching))
    return visible


def user_visible_res_stores(configuration, client_id, caching=False):
    """Extract a map of resources that client_id owns or can store data on.
    This is a wrapper combining user_owned_res_stores and
    user_allowed_res_stores.

    Returns a map from resource IDs to resource store node names.

    Resource IDs are anonymized unless explicitly configured otherwise.
    """
    visible = user_allowed_res_stores(configuration, client_id, caching)
    visible.update(user_owned_res_stores(configuration, client_id, caching))
    return visible


def user_allowed_user_confs(configuration, client_id, caching=False):
    """Extract a map of users that client_id can really view and maybe
    contact.

    Returns a map from user IDs to lists of user confs.

    User IDs are anonymized unless explicitly configured otherwise.
    """
    allowed = {}
    allowed_vgrids = user_vgrid_access(configuration, client_id,
                                       caching=caching)

    # Find all potential users from vgrid member and ownership

    user_map = get_user_map(configuration, caching)

    # Map only contains the raw user names - anonymize as requested

    anon_map = {}
    for user in user_map.keys():
        anon_map[user] = user_map[user][USERID]

    # Now select only the ones that actually still are allowed for that vgrid

    for (user, conf) in user_map.items():
        allowed[anon_map[user]] = conf
    return allowed


def user_visible_user_confs(configuration, client_id, caching=False):
    """Extract a map of users that client_id is allowed to view or contact.

    Returns a map from user IDs to user conf dictionaries.

    User IDs are anonymized unless explicitly configured otherwise, but
    the user confs are always raw.
    """
    visible = user_allowed_user_confs(configuration, client_id, caching)
    return visible


def resources_using_re(configuration, re_name, caching=False):
    """Find resources implementing the re_name runtime environment.

    Resources are anonymized unless explicitly configured otherwise.
    """
    resources = []
    resource_map = get_resource_map(configuration, caching)

    # Map only contains the raw resource names - anonymize as requested

    for (res_id, res) in resource_map.items():
        anon_id = resource_map[res_id][RESID]
        for env in resource_map[res_id][CONF]['RUNTIMEENVIRONMENT']:
            if env[0] == re_name:
                resources.append(anon_id)
    return resources


def get_re_provider_map(configuration, caching=False):
    """Find providers for all runtime environments in one go.

    Resources are anonymized unless explicitly configured otherwise.
    """
    provider_map = {}
    resource_map = get_resource_map(configuration, caching)

    # Map only contains the raw resource names - anonymize as requested

    for (res_id, res) in resource_map.items():
        anon_id = resource_map[res_id][RESID]
        for env in resource_map[res_id][CONF]['RUNTIMEENVIRONMENT']:
            re_name = env[0]
            provider_map[re_name] = provider_map.get(re_name, [])
            provider_map[re_name].append(anon_id)
    return provider_map


def unmap_resource(configuration, res_id):
    """Remove res_id from resource and vgrid maps - simply force refresh"""
    mark_resource_modified(configuration, res_id)
    mark_vgrid_modified(configuration, res_id)


def unmap_vgrid(configuration, vgrid_name):
    """Remove vgrid_name from vgrid map - simply force refresh"""
    mark_vgrid_modified(configuration, vgrid_name)


def unmap_inheritance(configuration, vgrid_name, cert_id):
    """Remove cert_id inherited access to all vgrid_name sub vgrids: Simply
    force refresh of those vgrids as cert_id was never really there.
    """
    (status, sub_vgrids) = vgrid_list_subvgrids(vgrid_name, configuration)
    for sub in sub_vgrids:
        mark_vgrid_modified(configuration, sub)


if "__main__" == __name__:
    import sys
    from shared.conf import get_configuration_object
    user_id = 'anybody'
    if len(sys.argv) > 1:
        user_id = sys.argv[1]
    runtime_env = 'PYTHON'
    if len(sys.argv) > 2:
        runtime_env = sys.argv[2]
    res_id = 'localhost.0'
    if len(sys.argv) > 3:
        res_id = sys.argv[3]
    conf = get_configuration_object()
    # Test listing alternative to vgrid_list_vgrids
    vgrid_list = get_vgrid_map_vgrids(conf)
    print "all vgrids: %s" % vgrid_list
    # Verify that old-fashioned user_allowed_vgrids matches user_vgrid_access
    vgrids_allowed = user_allowed_vgrids(conf, user_id)
    vgrids_allowed.sort()
    print "user allowed vgrids: %s" % vgrids_allowed
    vgrid_access = user_vgrid_access(conf, user_id)
    vgrid_access.sort()
    print "user access vgrids: %s" % vgrid_access
    print "user allow and access match: %s" % (vgrids_allowed == vgrid_access)
    # Verify that old-fashioned user_allowed_vgrids matches user_vgrid_access
    vgrids_allowed = user_allowed_vgrids(conf, user_id, inherited=True)
    vgrids_allowed.sort()
    print "inherit user allowed vgrids: %s" % vgrids_allowed
    vgrid_access = user_vgrid_access(conf, user_id, inherited=True)
    vgrid_access.sort()
    print "inherit user access vgrids: %s" % vgrid_access
    print "inherit user allow and access match: %s" % (
        vgrids_allowed == vgrid_access)
    # Verify that old-fashioned res_allowed_vgrids matches res_vgrid_access
    vgrids_allowed = res_allowed_vgrids(conf, res_id)
    vgrids_allowed.sort()
    print "res allowed vgrids: %s" % vgrids_allowed
    vgrid_access = res_vgrid_access(conf, res_id)
    vgrid_access.sort()
    print "res access vgrids: %s" % vgrid_access
    print "res allow and access match: %s" % (vgrids_allowed == vgrid_access)
    res_map = get_resource_map(conf)
    # print "raw resource map: %s" % res_map
    all_resources = res_map.keys()
    print "raw resource IDs: %s" % ', '.join(all_resources)
    all_anon = [res_map[i][RESID] for i in all_resources]
    print "raw anon names: %s" % ', '.join(all_anon)
    print
    user_map = get_user_map(conf)
    # print "raw user map: %s" % user_map
    all_users = user_map.keys()
    print "raw user IDs: %s" % ', '.join(all_users)
    all_anon = [user_map[i][USERID] for i in all_users]
    print "raw anon names: %s" % ', '.join(all_anon)
    print
    full_map = get_vgrid_map(conf)
    # print "raw vgrid map: %s" % full_map
    all_resources = full_map[RESOURCES].keys()
    print "raw resource IDs: %s" % ', '.join(all_resources)
    all_users = full_map[USERS].keys()
    print "raw user IDs: %s" % ', '.join(all_users)
    all_vgrids = full_map[VGRIDS].keys()
    print "raw vgrid names: %s" % ', '.join(all_vgrids)
    print
    user_access_confs = user_allowed_res_confs(conf, user_id)
    user_access_exes = user_allowed_res_exes(conf, user_id)
    user_access_stores = user_allowed_res_stores(conf, user_id)
    print "%s can access resources: %s" % \
          (user_id, ', '.join(user_access_confs.keys()))
    #(user_id, ', '.join([i for (i, j) in user_access_confs.items() if j]))
    print "%s can access exes: %s" % \
          (user_id, ', '.join(user_access_exes.keys()))
    #(user_id, ', '.join([i for (i, j) in user_access_exes.items() if j]))
    print "%s can access stores: %s" % \
          (user_id, ', '.join(user_access_stores.keys()))
    #(user_id, ', '.join([i for (i, j) in user_access_stores.items() if j]))
    user_owned_confs = user_owned_res_confs(conf, user_id)
    #user_owned_exes = user_owned_res_exes(conf, user_id)
    #user_owned_stores = user_owned_res_stores(conf, user_id)
    print "%s owns: %s" % \
          (user_id, ', '.join(user_owned_confs.keys()))
    user_visible_confs = user_visible_res_confs(conf, user_id)
    user_visible_exes = user_visible_res_exes(conf, user_id)
    user_visible_stores = user_visible_res_stores(conf, user_id)
    print "%s can view resources: %s" % \
          (user_id, ', '.join([i for i in user_visible_confs.keys()]))
    # print "full access exe dicts for %s:\n%s\n%s\n%s" % \
    #      (user_id, user_access_exes, user_owned_exes, user_visible_exes)
    # print "full access conf dicts for %s:\n%s\n%s\n%s" % \
    #      (user_id, user_access_confs, user_owned_confs, user_visible_confs)
    user_visible_users = user_visible_user_confs(conf, user_id)
    print "%s can view people: %s" % \
          (user_id, ', '.join([i for i in user_visible_users.keys()]))
    re_resources = resources_using_re(conf, runtime_env)
    print "%s in use on resources: %s" % \
          (runtime_env, ', '.join([i for i in re_resources]))
    direct_map = get_vgrid_map(conf, recursive=False)
    print "direct vgrid map vgrids: %s" % direct_map[VGRIDS]
    inherited_map = get_vgrid_map(conf, recursive=True)
    print "inherited vgrid map vgrids: %s" % inherited_map[VGRIDS]
