#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# --- BEGIN_HEADER ---
#
# importusers - Import users from text or xml file in provided uri
# Copyright (C) 2003-2019  The MiG Project lead by Brian Vinter
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

"""Import any missing users from provided URI"""

import os
import sys
import getopt
import re
import time
import urllib

import shared.returnvalues as returnvalues
from shared.base import fill_user, distinguished_name_to_user
from shared.conf import get_configuration_object
from shared.defaults import csrf_field, keyword_auto, cert_valid_days
from shared.functionality.sendrequestaction import main
from shared.handlers import get_csrf_limit, make_csrf_token
from shared.output import format_output
from shared.pwhash import generate_random_password, unscramble_password, \
    scramble_password
from shared.safeinput import valid_password_chars
from shared.useradm import init_user_adm, default_search, create_user, \
    search_users
from shared.vgridaccess import refresh_user_map


def usage(name='importusers.py'):
    """Usage help"""

    print """Import users from an external plain text or XML source URI.
Creates a local MiG user identified by DISTINGUISHED_NAME for each
new <item>DISTINGUISHED_NAME</item> in the XML or for each DISTINGUISHED_NAME
line in the text file.

Usage:
%(name)s [OPTIONS] URI [URI [...]]
Where URI may be an URL or local file and OPTIONS may be one or more of:
   -C CERT_PATH        Use CERT_PATH as client certificate
   -c CONF_FILE        Use CONF_FILE as server configuration
   -d DB_FILE          Use DB_FILE as user data base file
   -e EXPIRE           Set user account expiration to EXPIRE (epoch)
   -f                  Force operations to continue past errors
   -h                  Show this help
   -K KEY_PATH         Use KEY_PATH as client key
   -m VGRID            Make user a member of VGRID (multiple occurences allowed)
   -p PASSWORD         Optional PASSWORD to set for user (AUTO to generate one)
   -v                  Verbose output
""" % {'name': name}


def dump_contents(url, key_path=None, cert_path=None):
    """dump list of data lines from provided URL.
    Optional client key and certificate is supported.
    """

    # allow file dump at least until we get certificate based access

    browser = urllib.FancyURLopener(key_file=key_path,
                                    cert_file=cert_path)
    pipe = browser.open(url)
    data = pipe.read()
    browser.close()

    return data


def parse_contents(user_data):
    """Extract users from data dump - We simply catch all occurences of
    anything looking like a DN (/ABC=bla bla/DEF=more words/...) either in
    tags or in plain text line.
    """

    users = []
    for user_creds in re.findall('/[a-zA-Z]+=[^<\n]+', user_data):
        #print "DEBUG: handling user %s" % user_creds
        user_dict = distinguished_name_to_user(user_creds.strip())
        users.append(user_dict)
    return users


if '__main__' == __name__:
    (args, app_dir, db_path) = init_user_adm()
    conf_path = None
    key_path = None
    cert_path = None
    expire = int(time.time() + cert_valid_days * 24 * 60 * 60)
    force = False
    password = False
    verbose = False
    vgrids = []
    opt_args = 'C:c:d:e:fhK:m:p:v'
    try:
        (opts, args) = getopt.getopt(args, opt_args)
    except getopt.GetoptError, err:
        print 'Error: ', err.msg
        usage()
        sys.exit(1)

    for (opt, val) in opts:
        if opt == '-c':
            conf_path = val
        elif opt == '-d':
            db_path = val
        elif opt == '-e':
            expire = int(val)
        elif opt == '-f':
            force = True
        elif opt == '-h':
            usage()
            sys.exit(0)
        elif opt == '-C':
            cert_path = val
        elif opt == '-K':
            key_path = val
        elif opt == '-m':
            vgrids.append(val)
        elif opt == '-p':
            password = val
        elif opt == '-v':
            verbose = True
        else:
            print 'Error: %s not supported!' % opt
            sys.exit(1)

    if not args:
        print 'Must provide one or more URIs to import from'
        usage()
        sys.exit(1)

    users = []
    for url in args:
        url_dump = dump_contents(url, key_path, cert_path)
        users += parse_contents(url_dump)
    #print "DEBUG: raw users to import: %s" % users

    new_users = []
    for user_dict in users:
        id_search = default_search()
        id_search['distinguished_name'] = user_dict['distinguished_name']
        (configuration, hits) = search_users(id_search, conf_path, db_path,
                                             verbose)
        if hits:
            if verbose:
                print 'Not adding existing user: %(distinguished_name)s' % \
                      user_dict
            continue
        new_users.append(user_dict)
    #print "DEBUG: new users to import: %s" % new_users

    configuration = get_configuration_object()
    for user_dict in new_users:
        fill_user(user_dict)
        client_id = user_dict['distinguished_name']
        user_dict['comment'] = 'imported from external URI'
        if password == keyword_auto:
            print 'Auto generating password for user: %s' % client_id
            user_dict['password'] = generate_random_password(configuration)
        elif password:
            print 'Setting provided password for user: %s' % client_id
            user_dict['password'] = password
        else:
            print 'Setting empty password for user: %s' % client_id
            user_dict['password'] = ''

        # Encode password if set but not already encoded
        if user_dict['password']:
            if verbose:
                print 'Scrambling password for user: %s' % client_id
            user_dict['password'] = scramble_password(
                configuration.site_password_salt, user_dict['password'])

        # Force expire
        user_dict['expire'] = expire

        try:
            create_user(user_dict, conf_path, db_path, force, verbose)
        except Exception, exc:
            print exc
            continue
        print 'Created %s in user database and in file system' % client_id

    # NOTE: force update user_map before calling sendrequestaction!
    #       create_user does NOT necessarily update it due to caching time.
    refresh_user_map(configuration)

    # Needed for CSRF check in safe_handler
    form_method = 'post'
    csrf_limit = get_csrf_limit(configuration)
    target_op = 'sendrequestaction'
    os.environ.update({'SCRIPT_URL': '%s.py' % target_op,
                       'REQUEST_METHOD': form_method})
    for user_dict in new_users:
        fill_user(user_dict)
        client_id = user_dict['distinguished_name']
        csrf_token = make_csrf_token(configuration, form_method, target_op,
                                     client_id, csrf_limit)
        for name in vgrids:
            request = {'vgrid_name': [name], 'request_type': ['vgridmember'],
                       'request_text':
                       ['automatic request from importusers script'],
                       csrf_field: [csrf_token]}
            (output_objs, status) = main(client_id, request)
            if status == returnvalues.OK:
                print 'Request for %s membership in %s sent to owners' % \
                      (client_id, name)
            else:
                print 'Request for %s membership in %s with %s failed:' % \
                      (client_id, name, request)
                output_format = 'text'
                (ret_code, ret_msg) = status
                output = format_output(configuration, ret_code,
                                       ret_msg, output_objs, output_format)

                # Explicit None means error during output formatting

                if output is None:
                    print "ERROR: %s output formatting failed: %s" % \
                          (output_format, output_objs)
                    output = 'Error: output could not be correctly delivered!'
                else:
                    print output

    print '%d new users imported' % len(new_users)
