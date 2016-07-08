/*

  #
  # --- BEGIN_HEADER ---
  #
  # jquery.ajaxhelpers - jquery based ajax helpers for managers
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

  # This is a modified version of the Matlab Grid service by Jost Berthold.
  # Original license headers follow below.

*/

var center_class="class='centertext'";
var title_class="class='title'";
var border_class="class='border'";
function base_td(content) {
    return "<td>"+content+"</td>";
}
function attr_td(content, attr_helper) {
    return "<td "+attr_helper+">"+content+"</td>";
}
function center_td(content) {
    return attr_td(content, center_class);
}
function title_td(content) {
    return attr_td(content, title_class);
}
function border_td(content) {
    return attr_td(content, border_class);
}

function format_url(url) {
    return '<a class="link" href="'+url+'">'+url+'</a>';
}

function format_link(link_item) {
    var link = '<a ';
    if (link_item.id != undefined) {
        link += 'id="'+link_item.id+'" ';
    }
    if (link_item.class != undefined) {
        link += 'class="'+link_item.class+'" ';
    }
    if (link_item.title != undefined) {
        link += 'title="'+link_item.title+'" ';
    }
    if (link_item.target != undefined) {
        link += 'target="'+link_item.target+'" ';
    }
    link += 'href="'+link_item.destination+'">';
    if (link_item.text != undefined) {
        link += link_item.text;
    }
    link += '</a>';
    return link
}

function ajax_redb(_freeze) {
    console.debug("load runtime envs");
    //console.debug("empty table");
    $("#runtimeenvtable tbody").empty();
    $("#ajax_status").addClass("spinner iconleftpad");
    $("#ajax_status").html("Loading runtime envs ...");
    /* Request runtime envs list in the background and handle as soon as
    results come in */
    $.ajax({
      url: "?output_format=json;operation=list",
      type: "GET",
      dataType: "json",
      cache: false,
      success: function(jsonRes, textStatus) {
          console.debug("got response from list");
          var i = 0, j = 0;
          var rte, rte_hint, entry, error = "";
          /*
              Grab results from json response and insert rte items in table
              and append POST helpers to body to make confirm dialog work.
          */
          for (i=0; i<jsonRes.length; i++) {
              //console.debug("looking for content: "+ jsonRes[i].object_type);
              if (jsonRes[i].object_type == "error_text") {
                  console.error("list: "+jsonRes[i].text);
                  error += jsonRes[i].text;
              } else if (jsonRes[i].object_type == "html_form") {
                  entry = jsonRes[i].text;
                  if (entry.match(/function delete[0-9]+/)) {
                      //console.debug("append POST helper: "+entry);
                      $("body").append(entry);
                  }
              } else if (jsonRes[i].object_type == "runtimeenvironments") {
                  var runtimeenvs = jsonRes[i].runtimeenvironments;
                  for (j=0; j<runtimeenvs.length; j++) {
                      rte = runtimeenvs[j];
                      //console.info("found runtimeenv: "+rte.name);
                      var viewlink = format_link(rte.viewruntimeenvlink);
                      var dellink = "";
                      if(rte.ownerlink != undefined) {
                          dellink = format_link(rte.ownerlink);
                      }
                      rte_hint = center_class+" title='"+rte.providers+"'";
                      entry = "<tr>"+base_td(rte.name)+center_td(viewlink)+
                          center_td(dellink)+base_td(rte.description)+
                          attr_td(rte.resource_count, rte_hint)+
                          base_td(rte.created)+
                          "</tr>";
                      //console.debug("append entry: "+entry);
                      $("#runtimeenvtable tbody").append(entry);
                  }
              }
          }
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          if (error) {
              $("#ajax_status").append("<span class=\'errortext\'>"+
                                       "Error: "+error+"</span>");
          }
          $("#runtimeenvtable").trigger("update");

      },
      error: function(jqXHR, textStatus, errorThrown) {
          console.error("list failed: "+errorThrown);
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          $("#ajax_status").append("<span class=\'errortext\'>"+
                                   "Error: "+errorThrown+"</span>");
      }
  });
}

function ajax_freezedb(permanent_freeze) {
    console.debug("load archives");
    //console.debug("empty table");
    $("#frozenarchivetable tbody").empty();
    $("#ajax_status").addClass("spinner iconleftpad");
    $("#ajax_status").html("Loading archives ...");
    /* Request archive list in the background and handle as soon as
    results come in */
    $.ajax({
      url: "?output_format=json;operation=list",
      type: "GET",
      dataType: "json",
      cache: false,
      success: function(jsonRes, textStatus) {
          console.debug("got response from list");
          var i = 0, j = 0;
          var arch, entry, error = "";
          /*
              Grab results from json response and insert archive items in table
              and append POST helpers to body to make confirm dialog work.
          */
          for (i=0; i<jsonRes.length; i++) {
              //console.debug("looking for content: "+ jsonRes[i].object_type);
              if (jsonRes[i].object_type == "error_text") {
                  console.error("list: "+jsonRes[i].text);
                  error += jsonRes[i].text;
              } else if (jsonRes[i].object_type == "html_form") {
                  entry = jsonRes[i].text;
                  if (entry.match(/function delete[0-9]+/)) {
                      //console.debug("append POST helper: "+entry);
                      $("body").append(entry);
                  }
              } else if (jsonRes[i].object_type == "frozenarchives") {
                  var archives = jsonRes[i].frozenarchives;
                  for (j=0; j<archives.length; j++) {
                      arch = archives[j];
                      //console.info("found archive: "+arch.name);
                      var viewlink = format_link(arch.viewfreezelink);
                      var dellink = "";
                      if(!permanent_freeze) {
                          dellink = base_td(format_link(arch.delfreezelink));
                      }
                      entry = "<tr>"+base_td(arch.id)+center_td(viewlink)+
                          base_td(arch.name)+base_td(arch.created)+
                          center_td(arch.frozenfiles)+dellink+"</tr>";
                      //console.debug("append entry: "+entry);
                      $("#frozenarchivetable tbody").append(entry);
                  }
              }
          }
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          if (error) {
              $("#ajax_status").append("<span class=\'errortext\'>"+
                                       "Error: "+error+"</span>");
          }
          $("#frozenarchivetable").trigger("update");

      },
      error: function(jqXHR, textStatus, errorThrown) {
          console.error("list failed: "+errorThrown);
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          $("#ajax_status").append("<span class=\'errortext\'>"+
                                   "Error: "+errorThrown+"</span>");
      }
  });
}

function ajax_showfreeze(freeze_id, checksum) {
    console.debug("load archive "+freeze_id+" with "+checksum+" checksum");
    //console.debug("empty table");
    $("#frozenfilestable tbody").empty();
    $(".frozenarchivedetails tbody").empty();
    $("#ajax_status").addClass("spinner iconleftpad");
    $("#ajax_status").html("Loading archive "+freeze_id+" ...");
    /* Request archive list in the background and handle as soon as
    results come in */
    $.ajax({
      url: "?freeze_id="+freeze_id+";checksum="+checksum+
           ";output_format=json;operation=list",
      type: "GET",
      dataType: "json",
      cache: false,
      success: function(jsonRes, textStatus) {
          console.debug("got response from list");
          var i = 0, j = 0;
          var arch, entry, error = "";
          /*
              Grab results from json response and insert archive items in table
              and append POST helpers to body to make confirm dialog work.
          */
          for (i=0; i<jsonRes.length; i++) {
              //console.debug("looking for content: "+ jsonRes[i].object_type);
              if (jsonRes[i].object_type == "error_text") {
                  console.error("list: "+jsonRes[i].text);
                  error += " "+jsonRes[i].text;
              } else if (jsonRes[i].object_type == "html_form") {
                  entry = jsonRes[i].text;
                  if (entry.match(/function delete[0-9]+/)) {
                      //console.debug("append POST helper: "+entry);
                      $("body").append(entry);
                  }
              } else if (jsonRes[i].object_type == "frozenarchive") {
                  //console.debug("found frozenarchive");
                  var arch = jsonRes[i];
                  //console.debug("append details");
                  var published = "No";
                  if (arch.publish) {
                      published = "Yes ("+format_url(arch.publish_url)+")";
                  }
                  var location = "";
                  if (arch.location != undefined) {
                      var loc = arch.location;
                      for (j=0; j<loc.length; j++) {
                          location += "<tr>"+title_td("On "+loc[j][0])+
                              base_td(loc[j][1])+"</tr>";
                      }
                  }
                  entry = "<tr>"+title_td("ID")+base_td(arch.id)+"</tr><tr>"+
                      title_td("Name")+base_td(arch.name)+"</tr><tr>"+
                      title_td("Description")+border_td(arch.description)+
                      "</tr><tr>"+title_td("Published")+base_td(published)+
                      "</tr><tr>"+title_td("Creator")+base_td(arch.creator)+
                      "</tr><tr>"+title_td("Created")+base_td(arch.created)+
                      "</tr>"+location;
                  $(".frozenarchivedetails tbody").append(entry);
                  var files = arch.frozenfiles;
                  for (j=0; j<files.length; j++) {
                      file = files[j];
                      //console.info("found file: "+file.name);
                      entry = "<tr>"+base_td(file.name)+center_td(file.size)+
                          base_td(file.md5sum)+"</tr>";
                      //console.debug("append entry: "+entry);
                      $("#frozenfilestable tbody").append(entry);
                  }
              }
          }
          //console.debug("updated files table is: "+$("#frozenfilestable tbody").html());
          //console.debug("updated details table is: "+$(".frozenarchivedetails tbody").html());
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          if (error) {
              $("#ajax_status").append("<span class=\'errortext\'>"+
                                       "Error: "+error+"</span>");
          }
          $("#frozenfilestable").trigger("update");
      },
      error: function(jqXHR, textStatus, errorThrown) {
          console.error("list failed: "+errorThrown);
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          $("#ajax_status").append("<span class=\'errortext\'>"+
                                   "Error: "+errorThrown+"</span>");
      }
  });
}

function ajax_vgridman(vgrid_label, vgrid_links) {
    console.debug("load vgrids");
    //console.debug("empty table");
    $("#vgridtable tbody").empty();
    $("#ajax_status").addClass("spinner iconleftpad");
    $("#ajax_status").html("Loading "+vgrid_label+"s ...");
    /* Request vgrid list in the background and handle as soon as
    results come in */
    $.ajax({
      url: "?output_format=json;operation=list",
      type: "GET",
      dataType: "json",
      cache: false,
      success: function(jsonRes, textStatus) {
          console.debug("got response from list");
          var i, j, k;
          var vgrid, entry, error = "";
          /*
              Grab results from json response and insert vgrid items in table
              and append POST helpers to body to make confirm dialog work.
          */
          for (i=0; i<jsonRes.length; i++) {
              //console.debug("looking for content: "+ jsonRes[i].object_type);
              if (jsonRes[i].object_type == "error_text") {
                  console.error("list: "+jsonRes[i].text);
                  error += jsonRes[i].text;
              } else if (jsonRes[i].object_type == "html_form") {
                  entry = jsonRes[i].text;
                  if (entry.match(/function (rm|req)vgrid(owner|member)[0-9]+/)) {
                      //console.debug("append POST helper: "+entry);
                      $("body").append(entry);
                  }
              } else if (jsonRes[i].object_type == "vgrid_list") {
                  var vgrids = jsonRes[i].vgrids;
                  for (j=0; j<vgrids.length; j++) {
                      vgrid = vgrids[j];
                      //console.info("found vgrid: "+vgrid.name);
                      var viewlink = format_link(vgrid.viewvgridlink);
                      var adminlink = "";
                      var memberlink = "";
                      var activelinks = "";
                      if(vgrid.administratelink != undefined) {
                          adminlink = format_link(vgrid.administratelink);
                      }
                      if(vgrid.memberlink != undefined) {
                          memberlink = format_link(vgrid.memberlink);
                      }
                      entry = "<tr>"+base_td(vgrid.name)+center_td(viewlink)+
                          center_td(adminlink)+center_td(memberlink);
                      /* Adhere to vgrid_links list content and order */
                      for (k=0; k<vgrid_links.length; k++) {
                          activelinks = "";
                          var linkname = vgrid_links[k];
                          if (linkname == "files") {
                              if  (vgrid.sharedfolderlink != undefined) {
                                  activelinks = format_link(vgrid.sharedfolderlink);
                              }
                          } else if (linkname == "web") {
                              if(vgrid.enterprivatelink != undefined) {
                                  activelinks += format_link(vgrid.enterprivatelink);
                                  activelinks += " ";
                              }
                              if(vgrid.editprivatelink != undefined) {
                                  activelinks += format_link(vgrid.editprivatelink);
                                  activelinks += " ";
                              }
                              if(vgrid.enterpubliclink != undefined) {
                                  activelinks += format_link(vgrid.enterpubliclink);
                                  activelinks += " ";
                              }
                              if(vgrid.editpubliclink != undefined) {
                                  activelinks += format_link(vgrid.editpubliclink);
                                  activelinks += " ";
                              }
                          } else if (linkname == "scm") {
                              if(vgrid.ownerscmlink != undefined) {
                                  activelinks += format_link(vgrid.ownerscmlink);
                                  activelinks += " ";
                              }
                              if(vgrid.memberscmlink != undefined) {
                                  activelinks += format_link(vgrid.memberscmlink);
                                  activelinks += " ";
                              }
                          } else if (linkname == "tracker") {
                              if(vgrid.ownertrackerlink != undefined) {
                                  activelinks = format_link(vgrid.ownertrackerlink);
                              }
                              if(vgrid.membertrackerlink != undefined) {
                                  activelinks = format_link(vgrid.membertrackerlink);
                              }
                          } else if (linkname == "forum") {
                              if(vgrid.privateforumlink != undefined) {
                                  activelinks = format_link(vgrid.privateforumlink);
                              }
                              if(vgrid.publicforumlink != undefined) {
                                  activelinks = format_link(vgrid.publicforumlink);
                              }
                          } else if (linkname == "workflows") {
                              if(vgrid.privateworkflowslink != undefined) {
                                  activelinks = format_link(vgrid.privateworkflowslink);
                              }
                          } else if (linkname == "monitor") {
                              if(vgrid.privatemonitorlink != undefined) {
                                  activelinks = format_link(vgrid.privatemonitorlink);
                              }
                          } else {
                              console.error("unknown vgrid link: "+linkname+
                                            " or missing vgrid item link!");
                          }
                          entry += center_td(activelinks);
                      }
                      entry += "</tr>";
                      //console.debug("append entry: "+entry);
                      $("#vgridtable tbody").append(entry);
                  }
              }
          }
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          if (error) {
              $("#ajax_status").append("<span class=\'errortext\'>"+
                                       "Error: "+error+"</span>");
          }
          $("#vgridtable").trigger("update");

      },
      error: function(jqXHR, textStatus, errorThrown) {
          console.error("list failed: "+errorThrown);
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          $("#ajax_status").append("<span class=\'errortext\'>"+
                                   "Error: "+errorThrown+"</span>");
      }
  });
}

function ajax_resman() {
    console.debug("load resources");
    //console.debug("empty table");
    $("#resourcetable tbody").empty();
    $("#ajax_status").addClass("spinner iconleftpad");
    $("#ajax_status").html("Loading resources ...");
    /* Request resource list in the background and handle as soon as
    results come in */
    $.ajax({
      url: "?output_format=json;operation=list",
      type: "GET",
      dataType: "json",
      cache: false,
      success: function(jsonRes, textStatus) {
          console.debug("got response from list");
          var i, j, k;
          var resource, res_type, res_hint, rte_list, entry, error = "";
          /*
              Grab results from json response and insert resource items in table
              and append POST helpers to body to make confirm dialog work.
          */
          for (i=0; i<jsonRes.length; i++) {
              //console.debug("looking for content: "+ jsonRes[i].object_type);
              if (jsonRes[i].object_type == "error_text") {
                  console.error("list: "+jsonRes[i].text);
                  error += jsonRes[i].text;
              } else if (jsonRes[i].object_type == "html_form") {
                  entry = jsonRes[i].text;
                  if (entry.match(/function (rm|req)resowner[0-9]+/)) {
                      //console.debug("append POST helper: "+entry);
                      $("body").append(entry);
                  }
              } else if (jsonRes[i].object_type == "resource_list") {
                  var resources = jsonRes[i].resources;
                  for (j=0; j<resources.length; j++) {
                      resource = resources[j];
                      //console.info("found resource: "+resource.name);
                      var detailslink = "";
                      var ownerlink = "";
                      if(resource.resdetailslink != undefined) {
                          detailslink = format_link(resource.resdetailslink);
                      }
                      if (resource.resownerlink != undefined) {
                          ownerlink = format_link(resource.resownerlink);
                      }
                      res_type = "real";
                      if (resource.SANDBOX) {
                        res_type = 'sandbox'
                      }
                      res_hint = 'class="'+res_type+'res" title="'+res_type+
                          ' resource"';
                      rte_hint = center_class+' title="'+
                          resource.RUNTIMEENVIRONMENT.toString()+'"';
                      entry = "<tr>"+attr_td(resource.name, res_hint)+
                          center_td(detailslink)+center_td(ownerlink)+
                          attr_td(resource.RUNTIMEENVIRONMENT.length, rte_hint)+
                          center_td(resource.PUBLICNAME)+
                          center_td(resource.NODECOUNT)+
                          center_td(resource.CPUCOUNT)+
                          center_td(resource.MEMORY)+center_td(resource.DISK)+
                          center_td(resource.ARCHITECTURE)+"</tr>";
                      //console.debug("append entry: "+entry);
                      $("#resourcetable tbody").append(entry);
                  }
              }
          }
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          if (error) {
              $("#ajax_status").append("<span class=\'errortext\'>"+
                                       "Error: "+error+"</span>");
          }
          $("#resourcetable").trigger("update");

      },
      error: function(jqXHR, textStatus, errorThrown) {
          console.error("list failed: "+errorThrown);
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          $("#ajax_status").append("<span class=\'errortext\'>"+
                                   "Error: "+errorThrown+"</span>");
      }
  });
}

function ajax_people(protocols) {
    console.debug("load users");
    //console.debug("empty table");
    $("#usertable tbody").empty();
    $("#ajax_status").addClass("spinner iconleftpad");
    $("#ajax_status").html("Loading users ...");
    /* Request user list in the background and handle as soon as
    results come in */
    $.ajax({
      url: "?output_format=json;operation=list",
      type: "GET",
      dataType: "json",
      cache: false,
      success: function(jsonRes, textStatus) {
          console.debug("got response from list");
          var i = 0, j = 0, k = 0;
          var usr, link_name, proto, entry, error = "";
          /*
              Grab results from json response and insert user items in table
              and append POST helpers to body to make confirm dialog work.
          */
          for (i=0; i<jsonRes.length; i++) {
              //console.debug("looking for content: "+ jsonRes[i].object_type);
              if (jsonRes[i].object_type == "error_text") {
                  console.error("list: "+jsonRes[i].text);
                  error += jsonRes[i].text;
              } else if (jsonRes[i].object_type == "html_form") {
                  entry = jsonRes[i].text;
                  if (entry.match(/function send[a-z]+[0-9]+/)) {
                      //console.debug("append POST helper: "+entry);
                      $("body").append(entry);
                  }
              } else if (jsonRes[i].object_type == "user_list") {
                  var users = jsonRes[i].users;
                  for (j=0; j<users.length; j++) {
                      usr = users[j];
                      //console.info("found user: "+usr.name);
                      var viewlink = format_link(usr.userdetailslink);
                      var sendlink = "";
                      entry = "<tr>"+base_td(usr.name)+center_td(viewlink);
                      for (k=0; k<protocols.length; k++) {
                          proto = protocols[k];
                          link_name = "send"+proto+"link";
                          sendlink = "---";
                          if (usr[link_name] != undefined) {
                              sendlink = format_link(usr["send"+proto+"link"]);
                          }
                          entry += center_td(sendlink);
                      }
                      entry += "</tr>";
                      //console.debug("append entry: "+entry);
                      $("#usertable tbody").append(entry);
                  }
              }
          }
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          if (error) {
              $("#ajax_status").append("<span class=\'errortext\'>"+
                                       "Error: "+error+"</span>");
          }
          $("#usertable").trigger("update");

      },
      error: function(jqXHR, textStatus, errorThrown) {
          console.error("list failed: "+errorThrown);
          $("#ajax_status").removeClass("spinner iconleftpad");
          $("#ajax_status").empty();
          $("#ajax_status").append("<span class=\'errortext\'>"+
                                   "Error: "+errorThrown+"</span>");
      }
  });
}