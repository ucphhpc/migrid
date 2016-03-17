#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# --- BEGIN_HEADER ---
#
# datatransfer - import and export data in the backgroud
# Copyright (C) 2003-2016  The MiG Project lead by Brian Vinter
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

"""Manage data imports and exports"""

from binascii import hexlify
import glob
import os
import datetime
import time

import shared.returnvalues as returnvalues
from shared.base import client_id_dir
from shared.conf import get_resource_exe
from shared.transferfunctions import build_transferitem_object, \
     load_data_transfers, create_data_transfer, delete_data_transfer
from shared.defaults import all_jobs, job_output_dir, default_pager_entries
from shared.fileio import unpickle, pickle
from shared.functional import validate_input_and_cert
from shared.handlers import correct_handler
from shared.html import html_post_helper, themed_styles
from shared.init import initialize_main_variables, find_entry
from shared.ssh import copy_file_to_resource
from shared.validstring import valid_user_path


get_actions = ['show']
post_actions = ['import', 'export', 'deltransfer']
# TODO: add these internal targets on a separate tab without address and creds
#post_actions += ['move', 'copy', 'unpack', 'pack', 'remove']
valid_actions = get_actions + post_actions
valid_proto = [("http", "HTTP"), ("https", "HTTPS"), ("ftp", "FTP"),
               ("ftps", "FTPS"), ("sftp", "SFTP"), ("scp", "SCP"),
               ("webdav", "WebDAV"), ("webdavs", "WebDAVS"),
               ("ssh+rsync", "SSH + RSYNC"), ("rsync", "RSYNC")]


def signature():
    """Signature of the main function"""

    defaults = {'action': ['show'], 'transfer_id': [''], 'protocol': [''],
                'fqdn':[''], 'port': [''], 'src':[''], 'dst': [''],
                'username': [''], 'password': [''], 'key': [''], 'flags': ['']}
    return ['text', defaults]

def main(client_id, user_arguments_dict):
    """Main function used by front end"""

    (configuration, logger, output_objects, op_name) = \
        initialize_main_variables(client_id, op_header=False)
    client_dir = client_id_dir(client_id)
    defaults = signature()[1]
    (validate_status, accepted) = validate_input_and_cert(
        user_arguments_dict,
        defaults,
        output_objects,
        client_id,
        configuration,
        allow_rejects=False,
        )
    if not validate_status:
        return (accepted, returnvalues.CLIENT_ERROR)

    action = accepted['action'][-1]
    transfer_id = accepted['transfer_id'][-1]
    protocol = accepted['protocol'][-1]
    fqdn = accepted['fqdn'][-1]
    port = accepted['port'][-1]
    src_list = accepted['src']
    dst = accepted['dst'][-1]
    username = accepted['username'][-1]
    password = accepted['password'][-1]
    key = accepted['key'][-1]
    flags = accepted['flags']
    
    title_entry = find_entry(output_objects, 'title')
    title_entry['text'] = '%s data transfer' % configuration.short_title

    # jquery support for tablesorter and confirmation on "delete":

    title_entry['style'] = themed_styles(configuration)
    title_entry['javascript'] += '''
<script type="text/javascript" src="/images/js/jquery.js"></script>
<script type="text/javascript" src="/images/js/jquery.tablesorter.js"></script>
<script type="text/javascript" src="/images/js/jquery.tablesorter.pager.js"></script>
<script type="text/javascript" src="/images/js/jquery.tablesorter.widgets.js"></script>
<script type="text/javascript" src="/images/js/jquery-ui.js"></script>
<script type="text/javascript" src="/images/js/jquery.confirm.js"></script>

<script type="text/javascript">
    var fields = 1;
    var max_fields = 20;
    var src_input = "<input type=text size=60 name=src value='' /><br />";
    function addSource() {
        if (fields < max_fields) {
            $("#srcfields").append(src_input);
            fields += 1;
        } else {
            alert("Maximum " + max_fields + " source fields allowed!");
        }
    }
    function enableLogin(method) {
        $("#anonymous_choice").removeAttr("checked");
        $("#userpassword_choice").removeAttr("checked");
        $("#userkey_choice").removeAttr("checked");
        $("#username").prop("disabled", false);
        $("#password").prop("disabled", true);
        $("#key").prop("disabled", true);
        $("#login_fields").show();
        $("#password_entry").hide();
        $("#key_entry").hide();
        
        if (method == "password") {
            $("#userpassword_choice").prop("checked", "checked");
            $("#password").prop("disabled", false);
            $("#password_entry").show();
        } else if (method == "key") {
            $("#userkey_choice").prop("checked", "checked");
            $("#key").prop("disabled", false);
            $("#key_entry").show();
        } else {
            $("#anonymous_choice").prop("checked", "checked");
            $("#username").prop("disabled", true);
            $("#login_fields").hide();
        }
    }

    $(document).ready(function() {
          // init confirmation dialog
          $( "#confirm_dialog" ).dialog(
              // see http://jqueryui.com/docs/dialog/ for options
              { autoOpen: false,
                modal: true, closeOnEscape: true,
                width: 500,
                buttons: {
                   "Cancel": function() { $( "#" + name ).dialog("close"); }
                }
              });

        /* init create dialog */
        //$("#mode_tabs").tabs();
        enableLogin("anonymous");

        /* setup table with tablesorter initially sorted by 0 (id) */
        var sortOrder = [[0,0]];
        $("#datatransfertable").tablesorter({widgets: ["zebra", "saveSort"],
                                        sortList:sortOrder
                                        })
                               .tablesorterPager({ container: $("#pager"),
                                        size: %s
                                        });
    });
</script>
''' % default_pager_entries
    output_objects.append({'object_type': 'header', 'text'
                           : 'Manage background data transfers'})
    output_objects.append({'object_type': 'html_form',
                           'text':'''
 <div id="confirm_dialog" title="Confirm" style="background:#fff;">
  <div id="confirm_text"><!-- filled by js --></div>
   <textarea cols="40" rows="4" id="confirm_input"
       style="display:none;"></textarea>
 </div>
'''                       })

    if not action in valid_actions:
        output_objects.append({'object_type': 'error_text', 'text'
                               : 'Invalid action "%s" (supported: %s)' % \
                               (action, ', '.join(valid_actions))})
        return (output_objects, returnvalues.CLIENT_ERROR)

    if action in post_actions:
        if not correct_handler('POST'):
            output_objects.append(
                {'object_type': 'error_text', 'text'
                 : 'Only accepting POST requests to prevent unintended updates'})
            return (output_objects, returnvalues.CLIENT_ERROR)

    (load_status, transfer_map) = load_data_transfers(configuration, client_id)
    if not load_status:
        transfer_map = {}

    if action in get_actions:
        datatransfers = []
        output_objects.append({'object_type': 'sectionheader', 'text'
                          : 'Existing Transfers'})
        output_objects.append({'object_type': 'table_pager',
                               'entry_name': 'transfers',
                               'default_entries': default_pager_entries})
        for (saved_id, transfer_dict) in transfer_map.items():
            transfer_item = build_transferitem_object(configuration,
                                                      transfer_dict)
            saved_id = transfer_item['transfer_id']
            transfer_item['status'] = transfer_item.get('status', 'PARSE')            
            transfer_item['viewtransferlink'] = {
                'object_type': 'link',
                #'destination': "showtransfer.py?transfer_id=%s" % saved_id,
                'destination': '',
                'class': 'infolink', 
                'title': 'View data transfer %s' % saved_id,
                'text': ''}
            js_name = 'delete%s' % hexlify(saved_id)
            helper = html_post_helper(js_name, 'datatransfer.py',
                                      {'transfer_id': saved_id,
                                       'action': 'deltransfer'})
            output_objects.append({'object_type': 'html_form', 'text': helper})
            transfer_item['deltransferlink'] = {
                'object_type': 'link', 'destination':
                "javascript: confirmDialog(%s, '%s');" % \
                (js_name, 'Really remove %s?' % saved_id),
                'class': 'removelink', 'title': 'Remove %s' % \
                saved_id, 'text': ''}
            datatransfers.append(transfer_item)
        output_objects.append({'object_type': 'datatransfers', 'datatransfers'
                              : datatransfers})

        output_objects.append({'object_type': 'sectionheader', 'text'
                          : 'Create Transfer'})
        html = '''
<table class="adddatatransfer">
<tr><td>
Fill in the data transfer details below to request a new background data
transfer task.
Source can be a path or a wild card pattern using "*" and "?" to match one
or more characters.
Destination is a single location to transfer the data to. It is considered in
relation to your user home for <em>import</em> requests. Source is similarly
considered in relation to your user home in <em>export</em> requests.
Destination is a always handled as a directory path to transfer source files
into.<br/>
<form method="post" action="datatransfer.py">
<table class="adddatatransfer">
<tr><td>
<input type=radio name=action checked value="import" />import data
<input type=radio name=action value="export" />export data
</td></tr>
<tr><td>
Transfer ID:<br />
<input type=text size=60 name=transfer_id value="" />
</td></tr>
<tr><td>
<select name=protocol>
'''
        # select first in list
        selected = 'selected'
        for (key, val) in valid_proto:
            html += '<option %s value="%s">%s</option>' % (selected, key, val)
            selected = ''
        html += '''
</select>
Host:
<input type=text size=30 name=fqdn value="" />
Port:
<input type=text size=6 name=port value="" />
</td></tr>
<tr><td>
<input id="anonymous_choice" type=radio onclick="enableLogin(\'anonymous\');" />
anonymous access
<input id="userpassword_choice" type=radio onclick="enableLogin(\'password\');" />
login with password
<input id="userkey_choice" type=radio onclick="enableLogin(\'key\');" />
login with key
</td></tr>
<tr id="login_fields" style="display: none;"><td>
Username:<br />
<input id="username" type=text size=60 name=username value="" />
<br/>
<span id="password_entry">
Password:<br />
<input id="password" type=password size=60 name=password value="" />
</span>
<span id="key_entry">
Key:<br />
<input id="key" type=text size=60 name=key value="" />
</span>
</td></tr>
<tr><td>
Source path(s):<br />
<div id="srcfields">
<input type=text size=60 name=src value="" /><br />
</div>
<input id="addsrcbutton" type="button" onclick="addSource(); return false;"
    value="Add another source field" />
</td></tr>
<tr><td>
Destination path:<br />
<input type=text size=60 name=dst value="" />
</td></tr>
<tr><td>
Extra flags:<br />
<input type=text size=60 name=flags value="" />
</td></tr>
<tr><td>
<input type=submit value="Request transfer" />
</td></tr>
</table>
</form>
</td>
</tr>
</table>
'''
        output_objects.append({'object_type': 'html_form', 'text'
                              : html})
        return (output_objects, returnvalues.OK)
    elif action in post_actions:
        transfer_dict = transfer_map.get(transfer_id, {})
        if action == 'deltransfer':
            action_type = 'edit'
            if transfer_dict is None:
                output_objects.append(
                    {'object_type': 'error_text',
                     'text': 'existing transfer_id is required for delete'})
                return (output_objects, returnvalues.CLIENT_ERROR)
            (save_status, _) = delete_data_transfer(transfer_id, client_id,
                                                    configuration,
                                                    transfer_map)
            desc = "delete"
        else:
            action_type = 'transfer'
            if not fqdn:
                output_objects.append(
                    {'object_type': 'error_text', 'text'
                     : 'No host address provided!'})
                return (output_objects, returnvalues.CLIENT_ERROR)
            if not [src for src in src_list if src] or not dst:
                output_objects.append(
                    {'object_type': 'error_text',
                     'text': 'src and dst parameters required for all data'
                     'transfer'})
                return (output_objects, returnvalues.CLIENT_ERROR)
            # Make pseudo-unique ID based on msec time since epoch if not given
            if not transfer_id:
                transfer_id = "transfer-%d" % (time.time() * 1000)
            if transfer_dict:
                desc = "update"
            else:
                desc = "create"

            transfer_dict.update(
                {'transfer_id': transfer_id, 'action': action,
                 'protocol': protocol, 'fqdn': fqdn, 'port': port, 
                 'flags': flags, 'username': username, 'password': password,
                 'key':key, 'src': src_list, 'dst': dst, 'status': 'PARSE'})
            (save_status, _) = create_data_transfer(transfer_dict, client_id,
                                                    configuration,
                                                    transfer_map)
    else:
        output_objects.append({'object_type': 'error_text', 'text'
                              : 'Invalid data transfer action: %s' % action})
        return (output_objects, returnvalues.CLIENT_ERROR)

    if not save_status:
        output_objects.append(
            {'object_type': 'error_text', 'text'
             : 'Error in %s data transfer %s: '% (desc, transfer_id) + \
             'save updated transfers failed!'})
        return (output_objects, returnvalues.CLIENT_ERROR)

    output_objects.append({'object_type': 'text', 'text'
                          : '%sd transfer request %s.'  % (desc.title(),
                                                           transfer_id)
                           })
    output_objects.append({'object_type': 'link',
                           'destination': 'datatransfer.py',
                           'text': 'View data transfers'})
    return (output_objects, returnvalues.OK)
