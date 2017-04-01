X-APRS Daemon
#############

Convert an APRS feed into an X-APRS feed. This is an implementation of an X-APRS feed as proposed by Georg Lukas.

Warning
-------

**This is work-in-progress software!** Do **not** rely on the software or any service using this software (they must disclose this due to the AGPLv3 terms). It is highly experimental and the data it emits may be wrong or incomplete.

Dependencies
------------

* `aioxmpp <https://github.com/horazont/aioxmpp>`_ 0.8 (0.9 will not work, as this tool foolishly abuses internals)
* pygments


Usage
-----


::

    python3 xaprsd.py APRS-SERVER CALLSIGN

where APRS-SERVER must be a server providing an APRS full feed and CALLSIGN is the user name used to log into the server as well as the X-ARPS stream ``@from`` attribute value.


