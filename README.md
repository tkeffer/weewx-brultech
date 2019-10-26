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

## Manually configuring weewx.conf

This section is about manually configuring the configuration file, `weewx.conf`.

1. __Configure device driver__

    Insert a new stanza `[Brultech]` into your `weewx.conf` file that looks like this:

    ```ini
    [Brultech]
        # See the install instructions for how to configure the Brultech devices!!
    
        # The type of packet to use. Possible choices are GEMBin48NetTime,
        # GEMBin48Net, or GEMAscii:
        packet_type = GEMBin48NetTime
    
        # The type of connection to use. It should match a section below. 
        # Right now, only 'socket' is supported.
        connection = socket
    
        # How often to poll the device for data
        poll_interval = 5

        # Max number of times to try an I/O operation before declaring an error
        max_tries = 3
    
        driver = user.brultech.Brultech

        # The following is for socket connections: 
        [[socket]]
            host = 192.168.1.104
            port = 8083
            timeout = 20
            # After sending a command, how long to wait before looking for a response
            send_delay = 0.2

    
        [[sensor_map]]

    ```

    Be sure to set options `host` and `port` to their proper values for your network configuration.

2. __Set device driver__

    Tell WeeWX to use the Brultech device driver by setting `station_type`, 
under section `[Station]`, to `Brultech`:
 
     ```ini
    [Station]

        ...

        # Set to type of station hardware. There must be a corresponding stanza
        # in this file with a 'driver' parameter indicating the driver to be used.
        station_type = Brultech
    ```

3. __Configure new binding__

    Under stanza `[DataBindings]`, add a new binding `bt_binding` for the Brultech device. It should look like this: 

    ```ini
   [DataBindings]

     ...

        [[bt_binding]]
            # The database must match one of the sections in [Databases].
            # This is likely to be the only option you would want to change.
            database = bt_sqlite
            # The name of the table within the database
            table_name = archive
            # The manager handles aggregation of data for historical summaries
            manager = weewx.manager.DaySummaryManager
            # The schema defines the structure of the database.
            # It is *only* used when the database is created.
            schema = gem_schema.schema
    ```

4. __Configure new database__

    Under stanza `[Databases]`, add a new database `bt_sqlite`:
    ```ini
    [Databases]
   
    ...
   
        [[bt_sqlite]]
            database_name = weepwr.sdb
            database_type = SQLite  
    ```
   
5. __Use the new binding__ 

    Under stanza `[StdArchive]`, set the option `data_binding` to `bt_binding`:
    
    ```ini
   [StdArchive]

   ...
   
        data_binding = bt_binding         
   ```
   