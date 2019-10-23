weepwr
======

Extensions to the weewx weather system for Brultech energy monitors.

##Configuring your Brultech device
The Brultech energy monitors come with a bewildering array of modes and options. To keep things
simple, we have made a number of assumptions:

- The device will always be in server mode.
- The device driver always polls the device.

This makes it pretty easy to configure the device:

1. Set the GEM to "server mode".
Using a browser, connect to your GEM (http://192.168.1.101, in my case) 
and go to the "Application Settings" page. 
Set it to server mode, using port 8083:

    ![Application settings](images/server_mode.png)

    Click Apply.
    
2. Then click on the "Device Management" tab, and click the "Restart" button under 
*Restart Module*.

    ![Restart Module](images/restart_module.png)
    
    The monitor will reboot and be ready to receive commands from the driver
    
That's it!

## Configuring weewx.conf
Insert a stanza into your weewx.conf file that looks like:

```ini
[Brultech]

    # See the install instructions for how to configure the Brultech devices!!
    
    # The type of packet to use:
    packet_type = GEMBin48NetTime
    
    # The type of connection to use. It should match a section below. 
    # Right now, only 'socket' is supported.
    connection = socket
    
    # How often to poll the device for data
    poll_interval = 5

    # Max number of times to try an I/O operation before declaring an error
    max_tries = 3
    
    # What units the temperature sensors will be in:
    temperature_unit = degree_F

    [[socket]]
        # The following is for socket connections: 
        host = 192.168.1.104
        port = 8083
        timeout = 20
        # After sending a command, how long to wait before looking for a response    
        send_delay = 0.2

    
    [[sensor_map]]
```

Be sure to set options `host` and `port` to their proper values for your network configuration.

Then set option `station_type`, under section `[Station]` to `Brultech`:
 
 ```ini
[Station]

    # Description of the station location
    location = "My Little Town, Oregon"

    ...

    # Set to type of station hardware. There must be a corresponding stanza
    # in this file with a 'driver' parameter indicating the driver to be used.
    station_type = Brultech
```