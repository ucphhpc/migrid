#!/bin/bash
#
#	/etc/rc.d/init.d/MiG
#
#	MiG is a Grid middleware with minimal installation requirements
#
#	Recognized arguments:
#	    start   - start MiG system components
#	    stop    - terminate MiG system components
#	    restart - terminate and start MiG system 
#	    status  - report MiG system component's status
#
#	Customization of the MiG installation should be specified by
#	variables in /etc/sysconfig/MiG
#
# Made from the template /usr/share/doc/initscripts-X/sysinitvfiles
# from our CentOS installation
#
# <tags ...>
#
# chkconfig: - 90 10
# description: MiG is a Grid solution with minimal installation requirements
# processname: grid_script.py(default)
# processname: grid_monitor.py
# processname: grid_sshmux.py
# processname: grid_events.py
# processname: grid_openid.py
# processname: grid_sftp.py
# processname: grid_davs.py
# processname: grid_ftps.py
# config: /etc/sysconfig/MiG
# 

# Source function library.
. /etc/init.d/functions

# <define any local shell functions used by the code that follows>

# first, pull in custom configuration (if it exists):
if [ -f /etc/sysconfig/MiG ]; then
    . /etc/sysconfig/MiG
fi
# define default locations and user for MiG if not set:
if [ -z "$MIG_USER" ]; then 
    MIG_USER=mig
fi
if [ -z "$MIG_PATH" ]; then
    MIG_PATH=/home/${MIG_USER}
fi
# more configurable paths:
if [ -z "$MIG_STATE" ]; then 
    MIG_STATE=${MIG_PATH}/state
fi
if [ -z "$MIG_CODE" ]; then 
    MIG_CODE=${MIG_PATH}/mig
fi
if [ -n "$MIG_CONF" ]; then 
    CUSTOMCONF="MIG_CONF=$MIG_CONF "
fi
# you probably do not want to modify these...
MIG_LOG=${MIG_STATE}/log
MIG_SCRIPT=${MIG_CODE}/server/grid_script.py
MIG_MONITOR=${MIG_CODE}/server/grid_monitor.py
MIG_SSHMUX=${MIG_CODE}/server/grid_sshmux.py
MIG_EVENTS=${MIG_CODE}/server/grid_events.py
MIG_OPENID=${MIG_CODE}/server/grid_openid.py
MIG_SFTP=${MIG_CODE}/server/grid_sftp.py
MIG_DAVS=${MIG_CODE}/server/grid_davs.py
MIG_FTPS=${MIG_CODE}/server/grid_ftps.py
DELAY=5

start_script() {
	echo -n "Starting MiG server daemon: "
	daemon --user ${MIG_USER} \
	           "$CUSTOMCONF ${MIG_SCRIPT} > ${MIG_LOG}/server.out 2>&1 &"
	RET=$?
	if [ $RET -ne 0 ]; then 
	    failure
	    exit $RET
	else 
	    # some input for the mig server...
	    echo "" >> ${MIG_CODE}/server/server.stdin
	    success
	fi
	echo
}
start_monitor() {
	echo -n "Starting MiG monitor daemon:"
	daemon --user ${MIG_USER} \
	           "$CUSTOMCONF ${MIG_MONITOR} > ${MIG_LOG}/monitor.out 2>&1 &"
	RET2=$?
	[ $RET2 ] && success
	echo
	[ $RET2 ] || echo "Warning: Monitor not started."
	echo
}
start_sshmux() {
	echo -n "Starting MiG SSHMux daemon:"
	daemon --user ${MIG_USER} \
	           "$CUSTOMCONF ${MIG_SSHMUX} > ${MIG_LOG}/sshmux.out 2>&1 &"
	RET2=$?
	[ $RET2 ] && success
	echo
	[ $RET2 ] || echo "Warning: SSHMux not started."
	echo
}
start_events() {
	echo -n "Starting MiG events daemon:"
	daemon --user ${MIG_USER} \
	           "$CUSTOMCONF ${MIG_EVENTS} > ${MIG_LOG}/events.out 2>&1 &"
	RET2=$?
	[ $RET2 ] && success
	echo
	[ $RET2 ] || echo "Warning: Events not started."
	echo
}
start_openid() {
	echo -n "Starting MiG OpenID daemon:"
	daemon --user ${MIG_USER} \
	           "$CUSTOMCONF ${MIG_OPENID} > ${MIG_LOG}/openid.out 2>&1 &"
	RET2=$?
	[ $RET2 ] && success
	echo
	[ $RET2 ] || echo "Warning: OpenID not started."
	echo
}
start_sftp() {
	echo -n "Starting MiG SFTP daemon:"
	daemon --user ${MIG_USER} \
	           "$CUSTOMCONF ${MIG_SFTP} > ${MIG_LOG}/sftp.out 2>&1 &"
	RET2=$?
	[ $RET2 ] && success
	echo
	[ $RET2 ] || echo "Warning: SFTP not started."
	echo
}
start_davs() {
	echo -n "Starting MiG DAVS daemon:"
	daemon --user ${MIG_USER} \
	           "$CUSTOMCONF ${MIG_DAVS} > ${MIG_LOG}/davs.out 2>&1 &"
	RET2=$?
	[ $RET2 ] && success
	echo
	[ $RET2 ] || echo "Warning: DAVS not started."
	echo
}
start_ftps() {
	echo -n "Starting MiG FTPS daemon:"
	daemon --user ${MIG_USER} \
	           "$CUSTOMCONF ${MIG_FTPS} > ${MIG_LOG}/ftps.out 2>&1 &"
	RET2=$?
	[ $RET2 ] && success
	echo
	[ $RET2 ] || echo "Warning: FTPS not started."

	touch /var/lock/subsys/MiG
	return $RET
}

start_all() {
    start_script
    start_monitor
    start_sshmux
    start_events
    start_openid
    start_sftp
    start_davs
    start_ftps
}

stop_script() {
	pid=`pidofproc ${MIG_SCRIPT}`
	if [ -z "$pid" ]; then
	    echo -n "MiG server is not running..."
	    failure
	    echo
	else
            # try a shutdown before killing it
	    echo -n "SHUTDOWN MiG server (pid $pid)"
	    echo SHUTDOWN >> $MIG_PATH/mig/server/server.stdin
	    sleep ${DELAY}
	    checkpid $pid
	    KILLED=$?
	    if [ $KILLED ]; then 
		success;
	    else 
		failure
		echo
		echo -n "Killing MiG server"
		killproc ${MIG_SCRIPT} -KILL;
	    fi
	    echo
	fi	
	rm -f /var/lock/subsys/MiG
	return $RET
}
stop_monitor() {
	echo -n "Shutting down MiG monitor: "
	killproc ${MIG_MONITOR}
	echo
}
stop_sshmux() {
	echo -n "Shutting down MiG sshmux: "
	killproc ${MIG_SSHMUX}
	echo
}
stop_events() {
	echo -n "Shutting down MiG events: "
	killproc ${MIG_EVENTS}
	echo
}
stop_openid() {
	echo -n "Shutting down MiG openid: "
	killproc ${MIG_OPENID}
	echo
}
stop_sftp() {
	echo -n "Shutting down MiG sftp: "
	killproc ${MIG_SFTP}
	echo
}
stop_davs() {
	echo -n "Shutting down MiG davs: "
	killproc ${MIG_DAVS}
	echo
}
stop_ftps() {
	echo -n "Shutting down MiG ftps: "
	killproc ${MIG_FTPS}
	echo
}

stop_all() {
    stop_monitor
    stop_sshmux
    stop_events
    stop_openid
    stop_sftp
    stop_davs
    stop_ftps
    stop_script
}

status_script() {
    status ${MIG_SCRIPT}
}
status_monitor() {
    status ${MIG_MONITOR}
}
status_sshmux() {
    status ${MIG_SSHMUX}
}
status_events() {
    status ${MIG_EVENTS}
}
status_openid() {
    status ${MIG_OPENID}
}
status_sftp() {
    status ${MIG_SFTP}
}
status_davs() {
    status ${MIG_DAVS}
}
status_ftps() {
    status ${MIG_FTPS} 
}

status_all() {
    status_script
    status_monitor
    status_sshmux
    status_events
    status_openid
    status_sftp
    status_davs
    status_ftps
}


# Force valid target
case "$2" in
    script|monitor|sshmux|events|openid|sftp|davs|ftps)
        TARGET="$2"
	;;
    *)
        TARGET="all"
	;;
esac

case "$1" in
    start)
        eval "start_$TARGET"
	;;
    stop)
        eval "stop_$TARGET"
	;;
    status)
        eval "status_$TARGET"
	;;
    restart)
        eval "stop_$TARGET"
        eval "start_$TARGET"
	;;
#    reload)
#	<cause the service configuration to be reread, either with
#	kill -HUP or by restarting the daemons, in a manner similar
#	to restart above>
#	;;
#    condrestart)
#    	<Restarts the servce if it is already running. For example:>
#	[ -f /var/lock/subsys/<service> ] && restart || :
#    probe)
#	<optional.  If it exists, then it should determine whether
#	or not the service needs to be restarted or reloaded (or
#	whatever) in order to activate any changes in the configuration
#	scripts.  It should print out a list of commands to give to
#	$0; see the description under the probe tag below.>
#	;;
    *)
#	echo "Usage: <servicename> {start|stop|status|reload|restart[|probe]"
	echo "Usage: MiG {start|stop|status|restart} [daemon]"
	exit 1
	;;
esac
exit $?
