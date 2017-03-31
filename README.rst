X-APRS Daemon
#############

Convert an APRS feed into an X-APRS feed.

Dependencies
------------

* [``aioxmpp``](https://github.com/horazont/aioxmpp) 0.8 (0.9 will not work, as this tool foolishly abuses internals)
* pygments


Usage
-----


::

    python3 xaprsd.py APRS-SERVER CALLSIGN

where APRS-SERVER must be a server providing an APRS full feed and CALLSIGN is the user name used to log into the server as well as the X-ARPS stream ``@from`` attribute value.
