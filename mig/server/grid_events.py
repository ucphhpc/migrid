#!/usr/bin/python
# -*- coding: utf-8 -*-

#
# --- BEGIN_HEADER ---
#
# grid_events - event handler to monitor files and trigger actions
# Copyright (C) 2003-2018  The MiG Project lead by Brian Vinter
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

"""Event handler to monitor vgrid files for creation, modification and removal
and trigger any associated actions based on rule database.

Requires watchdog module (https://pypi.python.org/pypi/watchdog).
"""

import fnmatch
import glob
import logging
import logging.handlers
import os
import re
import signal
import sys
import tempfile
import time
import threading
import multiprocessing

try:
    from watchdog.observers import Observer
    from watchdog.events import PatternMatchingEventHandler, \
        FileModifiedEvent, FileCreatedEvent, FileDeletedEvent, \
        DirModifiedEvent, DirCreatedEvent, DirDeletedEvent
except ImportError:
    print 'ERROR: the python watchdog module is required for this daemon'
    sys.exit(1)

# Use the scandir module version if available:
# https://github.com/benhoyt/scandir
# Otherwise fail

try:
    from distutils.version import StrictVersion
    from scandir import scandir, walk, __version__ as scandir_version
    if StrictVersion(scandir_version) < StrictVersion("1.3"):

        # Important os.walk compatibility utf8 fixes were not added until 1.3

        raise ImportError('scandir version is too old >= 1.3 required')
except ImportError, exc:
    print 'ERROR: %s' % str(exc)
    sys.exit(1)

from shared.base import force_utf8
from shared.cmdapi import parse_command_args
from shared.conf import get_configuration_object
from shared.defaults import valid_trigger_changes, workflows_log_name, \
    workflows_log_size, workflows_log_cnt, csrf_field
from shared.events import get_path_expand_map
from shared.fileio import makedirs_rec, pickle, unpickle
from shared.handlers import get_csrf_limit, make_csrf_token
from shared.job import fill_mrsl_template, new_job
from shared.logger import daemon_logger, register_hangup_handler
from shared.serial import load
from shared.vgrid import vgrid_valid_entities, vgrid_add_workflow_jobs, \
    JOB_ID, JOB_CLIENT
from shared.vgridaccess import check_vgrid_access

# Global trigger rule dictionaries with rules for all VGrids

all_rules = {}
rule_hits = {}
dir_cache = {}

# Global miss cache to avoid wasting energy on repeated events without triggers

miss_cache = {}

# Global state helpers used in a number of functions and methods

shared_state = {}
shared_state['base_dir'] = None
shared_state['base_dir_len'] = 0
shared_state['file_inotify'] = None
shared_state['file_handler'] = None
shared_state['rule_handler'] = None
shared_state['rule_inotify'] = None

# Only cache rule misses for one minute at a time to catch rule updates.
# Run complete expire cycle if miss cache exceeds expire size.

_miss_cache_ttl = 60
_cache_expire_size = 10000

# Rate limit helpers

(_rate_limit_field, _settle_time_field) = ('rate_limit', 'settle_time')
_default_period = 'm'
_default_time = '0'
_unit_periods = {
    's': 1,
    'm': 60,
    'h': 60 * 60,
    'd': 24 * 60 * 60,
    'w': 7 * 24 * 60 * 60,
}
_hits_lock = threading.Lock()
_rule_monitor_lock = threading.Lock()
_trigger_event = '_trigger_event'
stop_running = multiprocessing.Event()
(configuration, logger) = (None, None)


def stop_handler(sig, frame):
    """A simple signal handler to quit on Ctrl+C (SIGINT) in main"""
    # Print blank line to avoid mix with Ctrl-C line
    print ''
    stop_running.set()


def make_fake_event(path, state, is_directory=False):
    """Create a fake state change event for path. Looks up path to see if the
    change is a directory or file.
    """

    file_map = {'modified': FileModifiedEvent,
                'created': FileCreatedEvent,
                'deleted': FileDeletedEvent}
    dir_map = {'modified': DirModifiedEvent,
               'created': DirCreatedEvent, 'deleted': DirDeletedEvent}
    if is_directory or os.path.isdir(path):
        fake = dir_map[state](path)
    else:
        fake = file_map[state](path)

    # mark it a trigger event

    setattr(fake, _trigger_event, True)
    return fake


def is_fake_event(event):
    """Check if event came from our trigger-X rules rather than a real file
    system change.
    """

    return getattr(event, _trigger_event, False)


def extract_time_in_secs(rule, field):
    """Get time in seconds for provided free form period field. The value is a
    integer or float string with optional unit letter appended. If no unit is
    given the default period is used and if all empty the default time is used.
    """

    pid = multiprocessing.current_process().pid

    limit_str = rule.get(field, '')
    if not limit_str:
        limit_str = str(_default_time)

    # NOTE: format is 3(s) or 52m
    # extract unit suffix letter and fall back to a raw value with default unit

    unit_key = _default_period
    if not limit_str[-1:].isdigit():
        val_str = limit_str[:-1]
        if limit_str[-1] in _unit_periods.keys():
            unit_key = limit_str[-1]
        else:

            # print "ERROR: invalid time value %s ... fall back to defaults" % \
            #      limit_str

            (unit_key, val_str) = (_default_period, _default_time)
    else:
        val_str = limit_str
    try:
        secs = float(val_str) * _unit_periods[unit_key]
    except Exception, exc:
        print '(%s) ERROR: failed to parse time %s (%s)!' % (pid,
                                                             limit_str, exc)
        secs = 0.0
    secs = max(secs, 0.0)
    return secs


def extract_hit_limit(rule, field):
    """Get rule rate limit as (max_hits, period_length)-tuple for provided
    rate limit field where the limit kicks in when more than max_hits happened
    within the last period_length seconds.
    """

    limit_str = rule.get(field, '')

    # NOTE: format is 3(/m) or 52/h
    # split string on slash and fall back to no limit and default unit

    parts = (limit_str.split('/', 1) + [_default_period])[:2]
    (number, unit) = parts
    if not number.isdigit():
        number = '-1'
    if unit not in _unit_periods.keys():
        unit = _default_period
    return (int(number), _unit_periods[unit])


def update_rule_hits(
    rule,
    path,
    change,
    ref,
    time_stamp,
):
    """Update rule hits history with event and remove expired entries. Makes
    sure to neither expire events needed for rate limit nor settle time
    checking.
    """

    pid = multiprocessing.current_process().pid
    (_, hit_period) = extract_hit_limit(rule, _rate_limit_field)
    settle_period = extract_time_in_secs(rule, _settle_time_field)

    # logger.debug('(%s) update rule hits at %s for %s and %s %s %s' % (
    #    pid,
    #    time_stamp,
    #    rule,
    #    path,
    #    change,
    #    ref,
    #    ))

    _hits_lock.acquire()
    rule_history = rule_hits.get(rule['rule_id'], [])
    rule_history.append((path, change, ref, time_stamp))
    max_period = max(hit_period, settle_period)
    period_history = [i for i in rule_history if time_stamp - i[3]
                      <= max_period]
    rule_hits[rule['rule_id']] = period_history
    _hits_lock.release()

    # logger.debug('(%s) updated rule hits for %s to %s' % (pid,
    #             rule['rule_id'], period_history))


def get_rule_hits(rule, limit_field):
    """find rule hit details"""

    pid = multiprocessing.current_process().pid

    if limit_field == _rate_limit_field:
        (hit_count, hit_period) = extract_hit_limit(rule, limit_field)
    elif limit_field == _settle_time_field:
        (hit_count, hit_period) = (1, extract_time_in_secs(rule,
                                                           limit_field))
    _hits_lock.acquire()
    rule_history = rule_hits.get(rule['rule_id'], [])
    res = (rule_history, hit_count, hit_period)
    _hits_lock.release()

    # logger.debug('(%s) get_rule_hits found %s' % (pid, res))

    return res


def get_path_hits(rule, path, limit_field):
    """find path hit details"""

    (rule_history, hit_count, hit_period) = get_rule_hits(rule,
                                                          limit_field)
    path_history = [i for i in rule_history if i[0] == path]
    return (path_history, hit_count, hit_period)


def above_path_limit(
    rule,
    path,
    limit_field,
    time_stamp,
):
    """Check path trigger history against limit field and return boolean
    indicating if the rate limit or settle time should kick in.
    """

    pid = multiprocessing.current_process().pid

    (path_history, hit_count, hit_period) = get_path_hits(rule, path,
                                                          limit_field)
    if hit_count <= 0 or hit_period <= 0:

        # logger.debug('(%s) no %s limit set' % (pid, limit_field))

        return False
    period_history = [i for i in path_history if time_stamp - i[3]
                      <= hit_period]

    # logger.debug('(%s) above path %s test found %s vs %d' % (pid,
    #             limit_field, period_history, hit_count))

    if len(period_history) >= hit_count:
        return True
    return False


def show_path_hits(rule, path, limit_field):
    """Return path hit details for printing"""

    pid = multiprocessing.current_process().pid

    msg = ''
    (path_history, hit_count, hit_period) = get_path_hits(rule, path,
                                                          limit_field)
    msg += \
        '(%s) found %d entries in trigger history and limit is %d per %s s' \
        % (pid, len(path_history), hit_count, hit_period)
    return msg


def wait_settled(
    rule,
    path,
    change,
    settle_secs,
    time_stamp,
):
    """Lookup recent change events on path and check if settle_secs passed
    since last one. Returns the number of seconds needed without further
    events for changes to be considered settled.
    """

    pid = multiprocessing.current_process().pid

    limit_field = _settle_time_field
    (path_history, _, hit_period) = get_path_hits(rule, path,
                                                  limit_field)
    period_history = [i for i in path_history if time_stamp - i[3]
                      <= hit_period]

    # logger.debug('(%s) wait_settled: path %s, change %s, settle_secs %s'
    #              % (pid, path, change, settle_secs))

    if not period_history:
        remain = 0.0
    else:

        # NOTE: the time_stamp - i[3] values are non-negative here
        # since hit_period >= 0.
        # Thus we can just take the smallest and subtract from settle_secs
        # to always wait the remaining part of settle_secs.

        remain = settle_secs - min([time_stamp - i[3] for i in
                                    period_history])

    # logger.debug('(%s) wait_settled: remain %.1f , period_history %s'
    #             % (pid, remain, period_history))

    return remain


def recently_modified(path, time_stamp, slack=2.0):
    """Check if path was actually recently modified and not just accessed.
    If atime and mtime are the same or if mtime is within slack from time_stamp
    we accept it as recently changed.
    """

    pid = multiprocessing.current_process().pid

    try:
        stat_res = os.stat(path)
        result = stat_res.st_mtime == stat_res.st_atime \
            or stat_res.st_mtime > time_stamp - slack
    except OSError, exc:

        # If we get an OSError, *path* is most likely deleted

        result = True

        # logger.debug('(%s) OSError: %s' % (pid, str(exc)))

    return result


def run_command(
    command_list,
    target_path,
    rule,
    configuration,
):
    """Run backend command built from command_list on behalf of user from
    rule and with args mapped to the backend variables.
    """

    pid = multiprocessing.current_process().pid
    client_id = rule['run_as']
    command_str = ' '.join(command_list)
    logger.info('(%s) run command for %s: %s' % (pid, target_path,
                                                 command_list))

    # logger.debug('(%s) run %s on behalf of %s' % (pid, command_str,
    #             client_id))

    (function, user_arguments_dict) = parse_command_args(configuration,
                                                         command_list)

    form_method = 'post'
    target_op = "%s" % function
    csrf_limit = get_csrf_limit(configuration)
    csrf_token = make_csrf_token(configuration, form_method, target_op,
                                 client_id, csrf_limit)
    user_arguments_dict[csrf_field] = [csrf_token]

    # logger.debug('(%s) import main from %s' % (pid, function))

    main = id
    txt_format = id
    try:
        exec 'from shared.functionality.%s import main' % function
        exec 'from shared.output import txt_format'

        # logger.debug('(%s) run %s on %s for %s' % \
        #              (pid, function, user_arguments_dict, client_id))

        # Fake HTTP POST manually setting fields required for CSRF check

        os.environ['HTTP_USER_AGENT'] = 'grid events daemon'
        os.environ['PATH_INFO'] = '%s.py' % function
        os.environ['REQUEST_METHOD'] = form_method.upper()
        # We may need a REMOTE_ADDR for gdplog call even if not really enabled
        os.environ['REMOTE_ADDR'] = '127.0.0.1'
        (output_objects, (ret_code, ret_msg)) = main(client_id,
                                                     user_arguments_dict)
    except Exception, exc:
        logger.error('(%s) failed to run %s main on %s: %s' %
                     (pid, function, user_arguments_dict, exc))
        import traceback
        logger.info('traceback:\n%s' % traceback.format_exc())
        raise exc
    logger.info('(%s) done running command for %s: %s' %
                (pid, target_path, command_str))

    # logger.debug('(%s) raw output is: %s' % (pid, output_objects))

    try:
        txt_out = txt_format(configuration, ret_code, ret_msg,
                             output_objects)
    except Exception, exc:
        txt_out = 'internal command output text formatting failed'
        logger.error('(%s) text formating failed: %s\nraw output is: %s %s %s'
                     % (pid, exc, ret_code, ret_msg, output_objects))
    if ret_code != 0:
        logger.warning('(%s) command finished but with error code %d :\n%s'
                       % (pid, ret_code, output_objects))
        raise Exception('command error: %s' % txt_out)

    # logger.debug('(%s) result was %s : %s:\n%s' % (pid, ret_code,
    #                                               ret_msg, txt_out))


class MiGRuleEventHandler(PatternMatchingEventHandler):

    """Rule pattern-matching event handler to take care of VGrid rule changes
    and update the global rule database.
    """

    def __init__(
        self,
        patterns=None,
        ignore_patterns=None,
        ignore_directories=False,
        case_sensitive=False,
    ):
        """Constructor"""

        PatternMatchingEventHandler.__init__(self, patterns,
                                             ignore_patterns,
                                             ignore_directories,
                                             case_sensitive)

    def __update_rule_monitor(
        self,
        configuration,
        src_path,
        state,
    ):

        pid = multiprocessing.current_process().pid

        if state == 'created':

            # logger.debug('(%s) Updating rule monitor for src_path: %s, event: %s'
            #              % (pid, src_path, state))

            print '(%s) Updating rule monitor for src_path: %s, event: %s' \
                % (pid, src_path, state)

            if os.path.exists(src_path):

                # _rule_monitor_lock.acquire()

                if not shared_state['rule_inotify']._wd_for_path.has_key(src_path):

                    # logger.debug('(%s) Adding watch for: %s' % (pid,
                    #             src_path))

                    shared_state['rule_inotify'].add_watch(
                        force_utf8(src_path))

                    # Fire 'modified' events for all dirs and files in subpath
                    # to ensure that all rule files are loaded

                    for ent in scandir(src_path):
                        if ent.is_dir(follow_symlinks=True):

                            # logger.debug('(%s) Dispatch DirCreatedEvent for: %s'
                            #         % (pid, ent.path))

                            shared_state['rule_handler'].dispatch(
                                DirCreatedEvent(ent.path))
                        elif ent.path.find(configuration.vgrid_triggers) \
                                > -1:

                            # logger.debug('(%s) Dispatch FileCreatedEvent for: %s'
                            #         % (pid, ent.path))

                            shared_state['rule_handler'].dispatch(
                                FileCreatedEvent(ent.path))

                # else:
                #    logger.debug('(%s) rule_monitor watch already exists for: %s'
                #                  % (pid, src_path))
        # else:
        #    logger.debug('(%s) unhandled event: %s for: %s' % (pid,
        #                 state, src_path))

    def update_rules(self, event):
        """Handle all rule updates"""

        pid = multiprocessing.current_process().pid
        state = event.event_type
        src_path = event.src_path

        if event.is_directory:
            self.__update_rule_monitor(configuration, src_path, state)
        elif src_path.endswith(configuration.vgrid_triggers):

            # logger.debug('(%s) %s -> Updating rule for: %s' % (pid,
            #             state, src_path))

            rel_path = src_path[len(configuration.vgrid_home):]
            vgrid_name = rel_path[:-len(configuration.vgrid_triggers)
                                  - 1]
            vgrid_prefix = os.path.join(configuration.vgrid_files_home,
                                        vgrid_name, '')
            logger.info('(%s) refresh %s rules from %s' %
                        (pid, vgrid_name, src_path))
            try:
                raw_rules = load(src_path)
                # NOTE: manually filter out any broken rules once and for all
                #       this is like if loaded with vgrid_triggers()
                new_rules = vgrid_valid_entities(configuration, vgrid_name,
                                                 'triggers', raw_rules)
            except Exception, exc:
                new_rules = []
                if state != 'deleted':
                    logger.error('(%s) failed to load event handler rules from %s (%s)'
                                 % (pid, src_path, exc))

            # logger.debug("(%s) loaded new rules from '%s':\n%s" % (pid,
            #             src_path, new_rules))

            # Remove all old rules for this vgrid and
            # leave rules for parent and sub-vgrids

            for target_path in all_rules.keys():
                all_rules[target_path] = [i for i in
                                          all_rules[target_path] if i['vgrid_name']
                                          != vgrid_name]
                remain_rules = [i for i in all_rules[target_path]
                                if i['vgrid_name'] != vgrid_name]
                if remain_rules:
                    all_rules[target_path] = remain_rules
                else:

                    # logger.debug('(%s) remain_rules for: %s \n%s'
                    #             % (pid, target_path, remain_rules))
                    # logger.debug('(%s) removing rules for: %s ' % (pid,
                    #             target_path))

                    del all_rules[target_path]
            for entry in new_rules:
                rule_id = entry['rule_id']
                path = entry['path']
                logger.info('(%s) updating rule: %s, path: %s, entry:\n%s'
                            % (pid, rule_id, path, entry))
                abs_path = os.path.join(vgrid_prefix, path)
                all_rules[abs_path] = all_rules.get(abs_path, []) \
                    + [entry]

            # logger.debug('(%s) all rules:\n%s' % (pid, all_rules))
        # else:
        #    logger.debug('(%s) %s skipping _NON_ rule file: %s' % (pid,
        #                 state, src_path))

    def on_modified(self, event):
        """Handle modified rule file"""

        self.update_rules(event)

    def on_created(self, event):
        """Handle new rule file"""

        self.update_rules(event)

    def on_deleted(self, event):
        """Handle deleted rule file"""

        self.update_rules(event)


class MiGFileEventHandler(PatternMatchingEventHandler):

    """File pattern-matching event handler to take care of VGrid file changes
    and the corresponding action triggers.
    """

    def __init__(
        self,
        patterns=None,
        ignore_patterns=None,
        ignore_directories=False,
        case_sensitive=False,
    ):
        """Constructor"""

        PatternMatchingEventHandler.__init__(self, patterns,
                                             ignore_patterns,
                                             ignore_directories,
                                             case_sensitive)

    def __workflow_log(
        self,
        configuration,
        vgrid_name,
        msg,
        level='info',
    ):
        """Wrapper to send a single msg to vgrid workflows page log file"""

        log_name = '%s.%s' % (configuration.vgrid_triggers,
                              workflows_log_name)
        log_path = os.path.join(configuration.vgrid_home, vgrid_name,
                                log_name)
        workflows_logger = logging.getLogger('workflows')
        workflows_logger.setLevel(logging.INFO)
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=workflows_log_size,
            backupCount=workflows_log_cnt - 1)
        formatter = \
            logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        handler.setFormatter(formatter)
        workflows_logger.addHandler(handler)
        if level == 'error':
            workflows_logger.error(msg)
        elif level == 'warning':
            workflows_logger.warning(msg)
        else:
            workflows_logger.info(msg)
        handler.flush()
        handler.close()
        workflows_logger.removeHandler(handler)

    def __workflow_err(
        self,
        configuration,
        vgrid_name,
        msg,
    ):
        """Wrapper to send a single error msg to vgrid workflows page log"""

        self.__workflow_log(configuration, vgrid_name, msg, 'error')

    def __workflow_warn(
        self,
        configuration,
        vgrid_name,
        msg,
    ):
        """Wrapper to send a single warning msg to vgrid workflows page log"""

        self.__workflow_log(configuration, vgrid_name, msg, 'warning')

    def __workflow_info(
        self,
        configuration,
        vgrid_name,
        msg,
    ):
        """Wrapper to send a single error msg to vgrid workflows page log"""

        self.__workflow_log(configuration, vgrid_name, msg, 'info')

    def __add_trigger_job_ent(
        self,
        configuration,
        event,
        rule,
        jobid,
    ):

        result = True
        pid = multiprocessing.current_process().pid

        vgrid_name = rule['vgrid_name']
        trigger_job_dir = os.path.join(
            configuration.vgrid_home, os.path.join(vgrid_name, os.path.join(
                '.%s.jobs' % configuration.vgrid_triggers, 'pending_states')))

        trigger_job_filepath = os.path.join(trigger_job_dir, jobid)

        if makedirs_rec(trigger_job_dir, configuration):
            trigger_job_dict = {
                'jobid': jobid,
                'owner': rule['run_as'],
                'rule': rule,
                'event': {},
            }
            src_path = ''
            if hasattr(event, 'src_path'):
                src_path = event.src_path
            dest_path = ''
            if hasattr(event, 'dest_path'):
                dest_path = event.dest_path
            trigger_job_dict['event']['src_path'] = src_path
            trigger_job_dict['event']['dest_path'] = dest_path
            trigger_job_dict['event']['time_stamp'] = event.time_stamp
            trigger_job_dict['event']['event_type'] = event.event_type
            trigger_job_dict['event']['is_directory'] = \
                event.is_directory

            # logger.debug('(%s) trigger_job_dict: %s' % (pid,
            #             trigger_job_dict))

            if not pickle(trigger_job_dict, trigger_job_filepath,
                          logger):
                result = False
        else:
            logger.error('(%s) Failed to create trigger job dir: %s'
                         % (pid, trigger_job_dir))
            result = False

        return result

    def __handle_trigger(
        self,
        event,
        target_path,
        rule,
    ):
        """Actually handle valid trigger for a specific event and the
        corresponding target_path pattern and trigger rule.
        """

        pid = multiprocessing.current_process().pid
        state = event.event_type
        src_path = event.src_path
        time_stamp = event.time_stamp
        _chain = getattr(event, '_chain', [(src_path, state)])
        rel_src = src_path[shared_state['base_dir_len']:].lstrip(os.sep)
        vgrid_prefix = os.path.join(
            shared_state['base_dir'], rule['vgrid_name'])
        logger.info('(%s) in handling of %s for %s %s' %
                    (pid, rule['action'], state, rel_src))
        above_limit = False

        # Run settle time check first to only trigger rate limit if settled

        for (name, field) in [('settle time', _settle_time_field),
                              ('rate limit', _rate_limit_field)]:
            if above_path_limit(rule, src_path, field, time_stamp):
                above_limit = True
                logger.warning('(%s) skip %s due to %s: %s' %
                               (pid, src_path, name, show_path_hits(
                                   rule, src_path, field)))
                self.__workflow_warn(configuration, rule['vgrid_name'],
                                     '(%s) skip %s trigger due to %s: %s' %
                                     (pid, rel_src, name, show_path_hits(
                                         rule, src_path, field)))
                break

        # TODO: consider if we should skip modified when just created

        # We receive modified events even when only atime changed - ignore them
        # but make sure we handle our fake trigger-modified events

        if state == 'modified' and not is_fake_event(event) \
                and not recently_modified(src_path, time_stamp):
            logger.info('(%s) skip %s which only changed atime' % (pid,
                                                                   src_path))
            self.__workflow_info(configuration, rule['vgrid_name'],
                                 'skip %s modified access time only event'
                                 % rel_src)
            return

        # Always update here to get trigger hits even for limited events

        update_rule_hits(rule, src_path, state, '', time_stamp)
        if above_limit:
            return
        logger.info('(%s) proceed with handling of %s for %s %s'
                    % (pid, rule['action'], state, rel_src))
        self.__workflow_info(configuration, rule['vgrid_name'],
                             'handle %s for %s %s' % (rule['action'],
                                                      state, rel_src))
        settle_secs = extract_time_in_secs(rule, _settle_time_field)
        if settle_secs > 0.0:
            wait_secs = settle_secs
        else:
            wait_secs = 0.0

            # logger.debug('(%s) no settle time for %s (%s)' % (pid,
            #             target_path, rule))

        while wait_secs > 0.0:
            logger.info('(%s) wait %.1fs for %s file events to settle down'
                        % (pid, wait_secs, src_path))
            self.__workflow_info(configuration, rule['vgrid_name'],
                                 'wait %.1fs for events on %s to settle'
                                 % (wait_secs, rel_src))
            time.sleep(wait_secs)

            # logger.debug('(%s) slept %.1fs for %s file events to settle down'
            #              % (pid, wait_secs, src_path))

            time_stamp += wait_secs
            wait_secs = wait_settled(rule, src_path, state,
                                     settle_secs, time_stamp)

        # TODO: perhaps we should discriminate on files and dirs here?
        # TODO: logger does not actually work here, only __workflow_X logs

        if rule['action'] in ['trigger-%s' % i for i in
                              valid_trigger_changes]:
            change = rule['action'].replace('trigger-', '')

            # Expand dynamic variables in argument once and for all

            expand_map = get_path_expand_map(rel_src, rule, state)
            for argument in rule['arguments']:
                filled_argument = argument
                for (key, val) in expand_map.items():
                    filled_argument = filled_argument.replace(key, val)

                # logger.debug('(%s) expanded argument %s to %s' % (pid,
                #             argument, filled_argument))

                self.__workflow_info(configuration, rule['vgrid_name'],
                                     'expanded argument %s to %s' %
                                     (argument, filled_argument))
                pattern = os.path.join(vgrid_prefix, filled_argument)
                for path in glob.glob(pattern):
                    rel_path = path[shared_state['base_dir_len']:]
                    _chain += [(path, change)]

                    # Prevent obvious trigger chain cycles

                    if (path, change) in _chain[:-1]:
                        flat_chain = ['%s : %s' % pair for pair in
                                      _chain]
                        chain_str = ' <-> '.join(flat_chain)
                        rel_chain_str = chain_str[
                            shared_state['base_dir_len']:]

                        logger.warning('(%s) breaking trigger cycle %s'
                                       % (pid, chain_str))
                        self.__workflow_warn(configuration,
                                             rule['vgrid_name'],
                                             'breaking trigger cycle %s'
                                             % rel_chain_str)
                        continue
                    fake = make_fake_event(path, change)
                    fake._chain = _chain
                    logger.info('(%s) trigger %s event on %s' % (pid,
                                                                 change, path))
                    self.__workflow_info(
                        configuration, rule['vgrid_name'],
                        'trigger %s event on %s' % (change, rel_path))
                    self.handle_event(fake)
        elif rule['action'] == 'submit':
            mrsl_fd = tempfile.NamedTemporaryFile(delete=False)
            mrsl_path = mrsl_fd.name

            # Expand dynamic variables in argument once and for all

            expand_map = get_path_expand_map(rel_src, rule, state)
            try:
                for job_template in rule['templates']:
                    mrsl_fd.truncate(0)

                    if not fill_mrsl_template(
                        job_template,
                        mrsl_fd,
                        rel_src,
                        state,
                        rule,
                        expand_map,
                        configuration,
                    ):
                        raise Exception('fill template failed')

                    # logger.debug('(%s) filled template for %s in %s'
                    #             % (pid, target_path, mrsl_path))

                    (success, msg, jobid) = new_job(
                        mrsl_path, rule['run_as'], configuration, False,
                        returnjobid=True)

                    if success:
                        self.__add_trigger_job_ent(configuration,
                                                   event, rule, jobid)

                        # update vgrid workflow jobs list
                        for vgrid in vgrid_list:
                            if vgrid != default_vgrid:
                                job_queue_entry = {
                                    JOB_ID: job_id,
                                    JOB_CLIENT: client_id
                                }
                                vgrid_add_workflow_jobs(configuration, vgrid,
                                                        [job_queue_entry])

                        logger.info('(%s) submitted job for %s: %s'
                                    % (pid, target_path, msg))
                        self.__workflow_info(configuration,
                                             rule['vgrid_name'],
                                             'submitted job for %s: %s' %
                                             (rel_src, msg))
                    else:
                        raise Exception(msg)
            except Exception, exc:
                logger.error('(%s) failed to submit job(s) for %s: %s'
                             % (pid, target_path, exc))
                self.__workflow_err(configuration, rule['vgrid_name'],
                                    'failed to submit job for %s: %s'
                                    % (rel_src, exc))
            try:
                os.remove(mrsl_path)
            except Exception, exc:
                logger.warning('(%s) clean up after submit failed: %s'
                               % (pid, exc))
        elif rule['action'] == 'command':

            # Expand dynamic variables in argument once and for all

            expand_map = get_path_expand_map(rel_src, rule, state)
            command_list = (rule['arguments'])[:1]
            for argument in (rule['arguments'])[1:]:
                filled_argument = argument
                for (key, val) in expand_map.items():
                    filled_argument = filled_argument.replace(key, val)
                self.__workflow_info(configuration, rule['vgrid_name'],
                                     'expanded argument %s to %s' %
                                     (argument, filled_argument))
                command_list.append(filled_argument)
            try:
                run_command(command_list, target_path, rule, configuration)
                logger.info('(%s) done running command for %s: %s' %
                            (pid, target_path, ' '.join(command_list)))
                self.__workflow_info(configuration, rule['vgrid_name'],
                                     'ran command: %s'
                                     % ' '.join(command_list))
            except Exception, exc:
                command_str = ' '.join(command_list)
                logger.error('(%s) failed to run command for %s: %s (%s)' %
                             (pid, target_path, command_str, exc))
                self.__workflow_err(configuration, rule['vgrid_name'],
                                    'failed to run command for %s: %s (%s)' %
                                    (rel_src, command_str, exc))
        else:
            logger.error('(%s) unsupported action: %s' % (pid,
                                                          rule['action']))

    def __update_file_monitor(self, event):
        """Updates file monitor using the global dir_cache"""

        pid = multiprocessing.current_process().pid
        state = event.event_type
        src_path = event.src_path
        is_directory = event.is_directory

        # If dir_modified is due to a file event we ignore it

        if is_directory and state == 'created':
            rel_path = src_path[shared_state['base_dir_len']:]

            # TODO: Optimize this such that only '.'
            # extracts vgrid_name and specific dir_cache ?

            vgrid_name = rel_path.split(os.sep)[0]
            if not dir_cache.has_key(vgrid_name):
                dir_cache[vgrid_name] = {}
            vgrid_dir_cache = dir_cache[vgrid_name]

            # logger.debug('(%s) Updating file monitor for src_path: %s, event: %s'
            #              % (pid, src_path, state))

            if os.path.exists(src_path) and os.path.isdir(src_path):
                try:
                    vgrid_dir_cache[rel_path] = {}
                    rel_path_ctime = os.path.getctime(src_path)
                    rel_path_mtime = os.path.getmtime(src_path)
                    add_vgrid_file_monitor_watch(configuration,
                                                 rel_path)
                    vgrid_dir_cache[rel_path]['mtime'] = rel_path_mtime

                    # Check if sub paths or files were changed
                    # For create this occurs by eg. mkdir -p 'path/subpath/subpath2'
                    # or 'cp -rf'

                    for ent in scandir(src_path):
                        if ent.is_dir(follow_symlinks=True):
                            vgrid_sub_path = ent.path[
                                shared_state['base_dir_len']:]

                            if not vgrid_sub_path in \
                                    vgrid_dir_cache.keys() or \
                                    vgrid_dir_cache[vgrid_sub_path]['mtime'] \
                                    < rel_path_ctime:

                                # logger.debug('(%s) %s -> Dispatch DirCreatedEvent for: %s'
                                #         % (pid, src_path, ent.path))

                                shared_state['file_handler'].dispatch(
                                    DirCreatedEvent(ent.path))
                        elif ent.is_file(follow_symlinks=True):

                            # logger.debug('(%s) %s -> Dispatch FileCreatedEvent for: %s'
                            #            % (pid, src_path, ent.path))

                            shared_state['file_handler'].dispatch(
                                FileCreatedEvent(ent.path))
                except OSError, exc:

                    # If we get an OSError, src_path was most likely deleted
                    # after os.path.exists check

                    # logger.debug('(%s) OSError: %s' % (pid, str(exc)))

                    pass

            # else:
            #    logger.debug('(%s) src_path: %s was deleted before current event: %s'
            #                  % (pid, src_path, state))

    def _get_event_id(self, event):
        """Build a simplified string form of event properties for use in the
        trigger miss cache.
        """
        return "path=%s;state=%s;isdir=%s" % (event.src_path, event.event_type,
                                              event.is_directory)

    def _update_recent_miss(self, event, hit):
        """Update the internal cache of recent events with no matching trigger
        rules for given event and hit status. Clears miss cache for event on
        a rule hit and adds the miss to the cache otherwise.
        On miss it additionally checks if the cache size exceeds the limit and
        if so expires all old misses.
        """
        pid = multiprocessing.current_process().pid

        event_id = self._get_event_id(event)
        if not hit:
            # logger.debug('(%s) update miss for %s: %s' % (pid, event_id,
            #                                               event.time_stamp))
            miss_cache[event_id] = event.time_stamp
        elif miss_cache.has_key(event_id):
            # logger.debug('(%s) delete miss cache for %s' % (pid, event_id))
            del miss_cache[event_id]
            return
        else:
            # logger.debug('(%s) no miss cache change for %s' % (pid,
            # event_id))
            return

        if len(miss_cache) < _cache_expire_size:
            return

        logger.info('(%s) expire all old entries in miss cache' % pid)
        now = time.time()
        for (event_id, time_stamp) in miss_cache.items():
            if time_stamp + _miss_cache_ttl < now:
                del miss_cache[event_id]
        logger.info('(%s) miss cache entries left after expire: %d' %
                    (pid, len(miss_cache)))

    def _recent_miss(self, event):
        """Check if we recently dismissed this kind of event. We store a small
        cache of recent events with no matching rules and check if given event
        is identical except the timestamp.
        """
        pid = multiprocessing.current_process().pid
        event_id = self._get_event_id(event)
        recent = miss_cache.get(event_id, -1)
        if recent + _miss_cache_ttl > time.time():
            # logger.debug('(%s) found recent miss for %s: %s' % (pid, event_id,
            #                                                     recent))
            return True
        else:
            # logger.debug('(%s) no recent miss for %s' % (pid, event_id))
            return False

    def run_handler(self, event):
        """Trigger any rule actions bound to file state change"""

        pid = multiprocessing.current_process().pid
        state = event.event_type
        src_path = event.src_path

        is_directory = event.is_directory

        # logger.debug('(%s) got %s event for src_path: %s, directory: %s' % \
        #             (pid, state, src_path, is_directory))
        # logger.debug('(%s) filter %s against %s' % (pid,
        #             all_rules.keys(), src_path))

        if self._recent_miss(event):
            logger.debug('(%s) skip cached miss %s event for src_path: %s' %
                         (pid, state, src_path))
            return

        rule_hit = False

        # Each target_path pattern has one or more rules associated

        for (target_path, rule_list) in all_rules.items():

            # Do not use ordinary fnmatch as it lets '*' match anything
            # including '/' which leads to greedy matching in subdirs

            recursive_regexp = fnmatch.translate(target_path)
            direct_regexp = recursive_regexp.replace('.*', '[^/]*')
            recursive_hit = re.match(recursive_regexp, src_path)
            direct_hit = re.match(direct_regexp, src_path)

            if direct_hit or recursive_hit:

                # logger.debug('(%s) matched %s for %s and/or %s' % (pid,
                #             src_path, direct_regexp, recursive_regexp))

                for rule in rule_list:

                    # Rules may listen for only file or dir events and with
                    # recursive directory search

                    if is_directory and not rule.get('match_dirs',
                                                     False):

                        # logger.debug('(%s) skip event %s handling for dir: %s'
                        #              % (pid, rule['rule_id'], src_path))

                        continue
                    if not is_directory and not rule.get('match_files',
                                                         True):

                        # logger.debug('(%s) skip %s event handling for file: %s'
                        #             % (pid, rule['rule_id'], src_path))

                        continue
                    if not direct_hit and not rule.get('match_recursive',
                                                       False):

                        # logger.debug('(%s) skip %s recurse event handling for: %s'
                        #              % (pid, rule['rule_id'], src_path))

                        continue
                    if not state in rule['changes']:

                        # logger.debug('(%s) skip %s %s event handling for: %s'
                        #         % (pid, rule['rule_id'], state,
                        #        src_path))

                        continue

                    # IMPORTANT: keep this vgrid access check last!
                    # It is far more computationally expensive than the simple
                    # checks above. We particularly want to filter the common
                    # storm of events from the system_imagesettings_dir_deleted
                    # trigger for '*' but only on dirs, before it gets here.

                    # User may have been removed from vgrid - log and ignore

                    # logger.debug('(%s) check valid user %s in %s for %s' % \
                    #              (pid, rule['run_as'], rule['vgrid_name'],
                    #               rule['rule_id']))

                    if not check_vgrid_access(configuration, rule['run_as'],
                                              rule['vgrid_name']):
                        logger.warning('(%s) no such user in vgrid: %s'
                                       % (pid, rule['run_as']))
                        continue

                    logger.info('(%s) trigger %s for src_path: %s -> %s'
                                % (pid, rule['action'], src_path,
                                    rule))

                    rule_hit = True

                    # TODO: Replace try/catch with an event queue or thread
                    #       pool setup

                    waiting_for_thread_resources = True
                    while waiting_for_thread_resources:
                        try:
                            worker = \
                                threading.Thread(target=self.__handle_trigger,
                                                 args=(event, target_path, rule))
                            worker.daemon = True
                            worker.start()
                            waiting_for_thread_resources = False
                        except threading.ThreadError, exc:

                            # logger.debug('(%s) Waiting for thread resources to handle trigger: %s'
                            #              % (pid, str(event)))

                            time.sleep(1)

            # else:
            #    logger.debug('(%s) skip %s with no matching rules'
            #                 % (pid, target_path))

        # Finally update rule miss cache for this event

        self._update_recent_miss(event, rule_hit)

    def handle_event(self, event):
        """Handle an event in the background so that it can block without
        stopping further event handling.
        We add a time stamp to have a sort of precise time for when the event
        was received. Still not perfect but better than comparing with 'now'
        values obtained deeply in handling calls.
        """

        pid = multiprocessing.current_process().pid

        event.time_stamp = time.time()

        # Update file_monitor and dir cache

        self.__update_file_monitor(event)

        # Run event handler

        self.run_handler(event)

    def on_modified(self, event):
        """Handle modified files"""

        self.handle_event(event)

    def on_created(self, event):
        """Handle created files"""

        self.handle_event(event)

    def on_deleted(self, event):
        """Handle deleted files"""

        self.handle_event(event)

    def on_moved(self, event):
        """Handle moved files: we translate a move to a created and a deleted
        event since the single event with src and dst does not really fit our
        model all that well. Furthermore inotify emits a created and a deleted
        event for a move between different filesystems or symlinked dirs.
        """

        for (change, path) in [('created', event.dest_path),
                               ('deleted', event.src_path)]:
            fake = make_fake_event(path, change, event.is_directory)
            self.handle_event(fake)


def add_vgrid_file_monitor_watch(configuration, path):
    """Adds file inotify watch for *path*"""

    pid = multiprocessing.current_process().pid

    vgrid_files_path = os.path.join(configuration.vgrid_files_home,
                                    path)

    if not shared_state['file_inotify']._wd_for_path.has_key(path):
        shared_state['file_inotify'].add_watch(force_utf8(vgrid_files_path))
    else:

        # logger.debug('(%s) Adding watch for: %s' % (pid,
        #             vgrid_files_path))

        logger.warning('(%s) file_monitor already exists for: %s'
                       % (pid, path))

    return True


def add_vgrid_file_monitor(configuration, vgrid_name, path):
    """Add file monitor for all dirs and subdirs in *path*, using the
    global dir_cache.
    """

    pid = multiprocessing.current_process().pid

    # Make sure we only have utf8 everywhere to avoid encoding issues
    path = force_utf8(path)

    retval = True
    vgrid_dir_cache = dir_cache[vgrid_name]
    vgrid_files_path = os.path.join(configuration.vgrid_files_home,
                                    path)

    if os.path.exists(vgrid_files_path):
        vgrid_files_path_mtime = os.path.getmtime(vgrid_files_path)

        # NOTE: make sure cache entry always gets initialized before use
        vgrid_dir_cache[path] = vgrid_dir_cache.get(path, {})
        vgrid_dir_cache[path]['mtime'] = vgrid_dir_cache[path].get('mtime', 0)

        try:
            add_vgrid_file_monitor_watch(configuration, path)

            if vgrid_files_path_mtime != vgrid_dir_cache[path]['mtime']:

                # Traverse dirs for subdirs created since last run

                for ent in scandir(vgrid_files_path):
                    if ent.is_dir(follow_symlinks=True):
                        vgrid_sub_path = ent.path[
                            shared_state['base_dir_len']:]
                        # Force utf8 everywhere to avoid encoding issues
                        vgrid_sub_path = force_utf8(vgrid_sub_path)
                        if not vgrid_sub_path in vgrid_dir_cache.keys():
                            retval &= add_vgrid_file_monitor(configuration,
                                                             vgrid_name,
                                                             vgrid_sub_path)

                vgrid_dir_cache[path]['mtime'] = vgrid_files_path_mtime
        except OSError, exc:
            # If we get an OSError, src_path was most likely deleted
            # after os.path.exists check or somehow not accessible

            logger.warning('(%s) add_vgrid_file_monitor failed on %s: %s' %
                           (pid, path, exc))
            del vgrid_dir_cache[path]
            return False

    return retval


def add_vgrid_file_monitors(configuration, vgrid_name):
    """Add file monitors for all dirs and subdirs for *vgrid_name*, using the
    global dir_cache.
    """

    pid = multiprocessing.current_process().pid

    vgrid_dir_cache = dir_cache[vgrid_name]

    vgrid_dir_cache_keys = vgrid_dir_cache.keys()
    for path in vgrid_dir_cache_keys:
        # Make sure we only have utf8 everywhere to avoid encoding issues
        path = force_utf8(path)
        vgrid_files_path = os.path.join(configuration.vgrid_files_home,
                                        path)
        if os.path.exists(vgrid_files_path):
            add_vgrid_file_monitor(configuration, vgrid_name, path)
        else:

            # logger.debug('(%s) Removing deleted dir: %s from dir_cache'
            #             % (pid, path))

            del vgrid_dir_cache[path]

    return True


def generate_vgrid_dir_cache(configuration, vgrid_base_path):
    """Generate directory cache for *vgrid_base_path*, using the global
    dir_cache.
    """

    pid = multiprocessing.current_process().pid

    vgrid_path = os.path.join(configuration.vgrid_files_home,
                              vgrid_base_path)

    if not dir_cache.has_key(vgrid_base_path):
        dir_cache[vgrid_base_path] = {}

    vgrid_dir_cache = dir_cache[vgrid_base_path]

    # Add VGrid root to directory cache

    vgrid_dir_cache[vgrid_base_path] = {}
    vgrid_dir_cache[vgrid_base_path]['mtime'] = \
        os.path.getmtime(vgrid_path)

    # logger.debug('(%s) Updating dir_cache %s: %s' % (pid,
    #             vgrid_base_path,
    #             vgrid_dir_cache[vgrid_base_path]['mtime']))

    # Add VGrid subdirs to directory cache

    for (root, dir_names, _) in walk(vgrid_path, followlinks=True):
        for dir_name in dir_names:
            dir_path = os.path.join(root, dir_name)
            dir_cache_path = dir_path[shared_state['base_dir_len']:]
            if not vgrid_dir_cache.has_key(dir_cache_path):
                vgrid_dir_cache[dir_cache_path] = {}
                vgrid_dir_cache[dir_cache_path]['mtime'] = \
                    os.path.getmtime(dir_path)

                # logger.debug('(%s) Updating dir_cache %s: %s' % (pid,
                #             dir_cache_path,
                #             vgrid_dir_cache[dir_cache_path]['mtime']))

    return True


def load_dir_cache(configuration, vgrid_name):
    """Load directory cache for *vgrid_name*, into the global dir_cache"""

    result = True

    pid = multiprocessing.current_process().pid

    vgrid_home_path = os.path.join(configuration.vgrid_home, vgrid_name)
    vgrid_dir_cache_filename = '.%s.dir_cache' \
        % configuration.vgrid_triggers
    vgrid_dir_cache_filepath = os.path.join(vgrid_home_path,
                                            vgrid_dir_cache_filename)

    # logger.debug('(%s) loading dir cache for: %s from: %s' % (pid,
    #             vgrid_name, vgrid_dir_cache_filename))

    # Load dir cache or generate new cache

    if os.path.exists(vgrid_dir_cache_filepath):

        # cache_t1 = time.time()

        loaded_dir_cache = unpickle(vgrid_dir_cache_filepath, logger,
                                    allow_missing=False)

        # cache_t2 = time.time()
        # logger.debug('(%s) Loaded vgrid_dir_cache for: %s in %s secs'
        #             % (pid, vgrid_name, str(cache_t2 - cache_t1)))

        if loaded_dir_cache is False:
            generate_cache = True
            logger.error('(%s) Failed to load vgrid_dir_cache for: %s from file: %s'
                         % (pid, vgrid_name, vgrid_dir_cache_filepath))
        else:
            generate_cache = False
            # TODO: once all caches are migrated we can remove this loop again
            # Make sure we only have utf8 everywhere to avoid encoding issues
            for old_path in [i for i in loaded_dir_cache.keys() if
                             isinstance(i, unicode)]:
                print "NOTE: forcing old cache entry %s to utf8" % [old_path]
                new_path = force_utf8(old_path)
                entry = loaded_dir_cache[old_path]
                del loaded_dir_cache[old_path]
                loaded_dir_cache[new_path] = entry

            dir_cache[vgrid_name] = loaded_dir_cache
    else:
        generate_cache = True

    if generate_cache:
        logger.info('(%s) Force generation of vgrid_dir_cache for: %s' %
                    (pid, vgrid_name))

        # cache_t1 = time.time()

        dir_cache[vgrid_name] = {}
        generate_vgrid_dir_cache(configuration, vgrid_name)

        # cache_t2 = time.time()
        # logger.debug('(%s) Generated new dir_cache for: %s in %s secs'
        #             % (pid, vgrid_name, str(cache_t2 - cache_t1)))

        result = save_dir_cache(vgrid_name)

    return result


def save_dir_cache(vgrid_name):
    """Save directory cache for *vgrid_name*, from the global dir_cache"""

    pid = multiprocessing.current_process().pid

    result = True

    dir_cache_filename = '.%s.dir_cache' % configuration.vgrid_triggers
    vgrid_dir_cache = dir_cache.get(vgrid_name, None)

    if vgrid_dir_cache is not None:
        vgrid_home_path = os.path.join(configuration.vgrid_home,
                                       vgrid_name)
        dir_cache_filepath = os.path.join(vgrid_home_path,
                                          dir_cache_filename)
        vgrid_dir_cache_keys = [key for key in vgrid_dir_cache.keys()
                                if key == vgrid_name
                                or key.startswith('%s%s' % (vgrid_name,
                                                            os.sep))]
        if len(vgrid_dir_cache_keys) == 0:
            logger.info('(%s) no dirs in cache for: %s' % (pid,
                                                           vgrid_name))
        else:
            logger.info('(%s) saving cache for: %s to file: %s' %
                        (pid, vgrid_name, dir_cache_filepath))
            pickle(vgrid_dir_cache, dir_cache_filepath, logger)

    return result


def active_targets(configuration, vgrid_name, base_dir):
    """Returns all active target paths for the vgrid with base_dir as
    root directory based on global all_rules dictionary. Uses base_dir with
    trailing slash to distinguish vgrids with shared prefix like Project and
    Project-Management.
    """
    base_dir_slash = base_dir + os.sep
    active = [i for i in all_rules.keys() if i.startswith(base_dir_slash)]
    return active


def monitor(configuration, vgrid_name):
    """Monitors the filesystem for changes and match/apply trigger rules.
    Each top vgrid gets its own process.
    Handling new vgrids is done through a special root vgrid with vgrid_name='.'.
    New vgrids are handled by '.' until grid_events is restarted, after restart
    they get their own process.
    """

    pid = multiprocessing.current_process().pid

    # TODO: We loose access to logger when called through multiprocessing

    print 'Starting monitor process with PID: %s for vgrid: %s' % (pid,
                                                                   vgrid_name)
    logger.info('Starting monitor process with PID: %s for vgrid: %s'
                % (pid, vgrid_name))

    # Set base_dir and base_dir_len

    shared_state['base_dir'] = os.path.join(configuration.vgrid_files_home)
    shared_state['base_dir_len'] = len(shared_state['base_dir'])

    # Allow e.g. logrotate to force log re-open after rotates
    register_hangup_handler(configuration)

    # Monitor rule configurations

    if vgrid_name == '.':
        vgrid_home = configuration.vgrid_home
        file_monitor_home = shared_state['base_dir']
        recursive_rule_monitor = False
    else:
        vgrid_home = os.path.join(configuration.vgrid_home, vgrid_name)
        file_monitor_home = os.path.join(shared_state['base_dir'], vgrid_name)
        recursive_rule_monitor = True

    rule_monitor = Observer()
    rule_patterns = [os.path.join(vgrid_home, '*')]
    shared_state['rule_handler'] = MiGRuleEventHandler(
        patterns=rule_patterns, ignore_directories=False, case_sensitive=True)

    rule_monitor.schedule(shared_state['rule_handler'], vgrid_home,
                          recursive=recursive_rule_monitor)
    rule_monitor.start()

    if len(rule_monitor._emitters) != 1:
        logger.error('(%s) Number of rule_monitor._emitters != 1' % pid)
        return 1
    rule_monitor_emitter = min(rule_monitor._emitters)
    if not hasattr(rule_monitor_emitter, '_inotify'):
        logger.error('(%s) rule_monitor_emitter require inotify' % pid)
        return 1
    shared_state['rule_inotify'] = rule_monitor_emitter._inotify._inotify

    logger.info('(%s) initializing file listener - may take some time'
                % pid)

    # monitor actual files to handle events for vgrid_files_home

    file_monitor = Observer()
    file_patterns = [os.path.join(file_monitor_home, '*')]
    shared_state['file_handler'] = MiGFileEventHandler(
        patterns=file_patterns, ignore_directories=False, case_sensitive=True)
    file_monitor.schedule(shared_state['file_handler'], file_monitor_home,
                          recursive=False)
    file_monitor.start()

    if len(file_monitor._emitters) != 1:
        logger.error('(%s) Number of file_monitor._emitters != 1' % pid)
        return 1
    file_monitor_emitter = min(file_monitor._emitters)
    if not hasattr(file_monitor_emitter, '_inotify'):
        logger.error('(%s) file_monitor require inotify' % pid)
        return 1
    shared_state['file_inotify'] = file_monitor_emitter._inotify._inotify

    logger.info('(%s) trigger rule refresh for: %s' % (pid, vgrid_name))

    # Fake touch event on all rule files to load initial rules

    logger.info('(%s) trigger load on all rule files (greedy) for: %s matching %s'
                % (pid, vgrid_name, rule_patterns[0]))

    # We manually walk and test to get the greedy "*" directory match behaviour
    # of the PatternMatchingEventHandler

    all_trigger_rules = []

    if recursive_rule_monitor:
        for (root, _, files) in walk(vgrid_home):
            if configuration.vgrid_triggers in files:
                rule_path = os.path.join(root,
                                         configuration.vgrid_triggers)
                all_trigger_rules.append(rule_path)
    else:
        for ent in scandir(vgrid_home):
            if configuration.vgrid_triggers in ent.name:
                rule_path = ent.path
                all_trigger_rules.append(rule_path)

    for rule_path in all_trigger_rules:

        # logger.debug('(%s) trigger load on rules in %s' % (pid,
        #             rule_path))

        shared_state['rule_handler'].dispatch(FileModifiedEvent(rule_path))

    # logger.debug('(%s) loaded initial rules:\n%s' % (pid, all_rules))

    # Add watches for directories

    if vgrid_name == '.':

        # logger.debug('(%s) Skipping dir_cache load for root dir: %s'
        #             % (pid, vgrid_name))

        pass
    else:

        # load_dir_cache_t1 = time.time()

        load_status = load_dir_cache(configuration, vgrid_name)

        # load_dir_cache_t2 = time.time()
        # logger.debug('(%s) load_dir_cache for: %s in %s secs' % (pid,
        #             vgrid_name, str(load_dir_cache_t2
        #             - load_dir_cache_t1)))

        if not load_status:
            logger.error('(%s) Failed to load / generate dir cache for: %s'
                         % (pid, vgrid_name))
            stop_running.set()

    activated = False
    while not stop_running.is_set():

        # NOTE: We delay launch of actual monitors until any rules are active,
        #       in order to avoid excessive load from vgrids without triggers.
        # TODO: consider making the detection and activation really dynamic.
        #       I.e. only watch dirs that any rule can ever match. Not trivial
        #       because it requires sync between rule and file monitors.

        if not activated:
            if active_targets(configuration, vgrid_name, file_monitor_home):
                # Start paths in vgrid_dir_cache to monitor
                print '(%s) init trigger handling for: %s' % (pid, vgrid_name)
                add_monitor_t1 = time.time()
                add_vgrid_file_monitors(configuration, vgrid_name)
                add_monitor_t2 = time.time()
                print '(%s) ready to handle triggers for: %s in %s secs' \
                      % (pid, vgrid_name, add_monitor_t2 - add_monitor_t1)
                logger.info('(%s) ready to handle triggers for: %s in %s secs'
                            % (pid, vgrid_name, add_monitor_t2
                               - add_monitor_t1))
                activated = True
            else:
                # Variable per-process delay to avoid thrashing
                delay = 60 + pid % 30
                logger.debug('(%s) no matching triggers for %s - sleep %ds' %
                             (pid, vgrid_name, delay))
                time.sleep(delay)

        # Once past the activation we just sleep in a responsive loop

        try:

            # Throttle down

            time.sleep(1)
        except KeyboardInterrupt:
            print '(%s) caught interrupt' % pid
            logger.info('(%s) caught interrupt' % pid)
            stop_running.set()

    # Only save cache if rules were actually activated so dirs were monitored
    if activated:
        print '(%s) Saving cache for vgrid: %s' % (pid, vgrid_name)
        logger.info('(%s) Saving cache for vgrid: %s' % (pid, vgrid_name))
        save_dir_cache(vgrid_name)

    print '(%s) Exiting monitor for vgrid: %s' % (pid, vgrid_name)
    logger.info('(%s) Exiting for vgrid: %s' % (pid, vgrid_name))

    return 0


if __name__ == '__main__':
    # Force no log init since we use separate logger
    configuration = get_configuration_object(skip_log=True)

    log_level = configuration.loglevel
    if sys.argv[1:] and sys.argv[1] in ['debug', 'info', 'warning',
                                        'error']:
        log_level = sys.argv[1]

    # Use separate logger

    logger = daemon_logger('events', configuration.user_events_log,
                           log_level)
    configuration.logger = logger

    # Allow e.g. logrotate to force log re-open after rotates
    register_hangup_handler(configuration)

    # Allow clean shutdown on SIGINT only to main process
    signal.signal(signal.SIGINT, stop_handler)

    if not configuration.site_enable_events:
        err_msg = "Event trigger support is disabled in configuration!"
        logger.error(err_msg)
        print err_msg
        sys.exit(1)

    print '''This is the MiG event handler daemon which monitors VGrid files
and triggers any configured events when target files are created, modifed or
deleted. VGrid owners can configure rules to trigger such events based on file
changes.

Set the MIG_CONF environment to the server configuration path
unless it is available in mig/server/MiGserver.conf
'''

    main_pid = os.getpid()
    print 'Starting Event handler daemon - Ctrl-C to quit'
    logger.info('(%s) Starting Event handler daemon' % main_pid)

    vgrid_monitors = {}

    # Start monitor for new/removed vgrids

    vgrid_name = '.'
    vgrid_monitors[vgrid_name] = \
        multiprocessing.Process(target=monitor, args=(configuration,
                                                      vgrid_name))

    # Each top vgrid gets is own process

    for ent in scandir(configuration.vgrid_home):
        vgrid_files_path = os.path.join(configuration.vgrid_files_home,
                                        ent.name)
        if os.path.isdir(ent.path) and os.path.isdir(vgrid_files_path):
            vgrid_name = ent.name
            vgrid_monitors[vgrid_name] = \
                multiprocessing.Process(target=monitor,
                                        args=(configuration, vgrid_name))

        # else:
        #    logger.debug('Skipping _NON_ vgrid: %s' % ent.path)

    for monitor in vgrid_monitors.values():
        monitor.start()

    logger.debug('(%s) Starting main loop' % main_pid)
    print "%s: Start main loop" % os.getpid()
    while not stop_running.is_set():
        try:

            # Throttle down

            time.sleep(1)
        except KeyboardInterrupt:
            stop_running.set()
            # NOTE: we can't be sure if SIGINT was sent to only main process
            #       so we make sure to propagate to all monitor children
            print "Interrupt requested - close monitors and shutdown"
            logger.info('(%s) Shut down monitors and wait' % os.getpid())
            for monitor in vgrid_monitors.values():
                mon_pid = monitor.pid
                if mon_pid is None:
                    continue
                logger.debug('send exit signal to monitor %s' % mon_pid)
                os.kill(mon_pid, signal.SIGINT)

            logger.info('Wait for monitors to clean up')
            for monitor in vgrid_monitors.values():
                mon_pid = monitor.pid
                logger.debug('wait for monitor %s: %s' % (mon_pid,
                                                          monitor.is_alive()))
                monitor.join(5)
                if monitor.is_alive():
                    logger.warning("force kill %s: %s" % (mon_pid,
                                                          monitor.is_alive()))
                    monitor.terminate()
                else:
                    logger.debug('monitor %s: done' % mon_pid)

            logger.info('(%s) Shut down: all monitors done' % os.getpid())
            print "All monitors finished shutting down"

    print 'Event handler daemon shutting down'
    logger.info('(%s) Event handler daemon shutting down' % main_pid)

    sys.exit(0)
