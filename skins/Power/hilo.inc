## summary statistics module for weewx skins
## Copyright Tom Keffer, Matthew Wall
## See LICENSE.txt for your rights
#errorCatcher Echo
#encoding UTF-8

#set $timespans = ['day', 'week', 'month', 'year']

## Get the list of observations from the configuration file, otherwise fallback
## to a very rudimentary set of observations.
#set $observations = $DisplayOptions.get('observations_stats', ['ch1_a_energy2'])

## use this span to determine whether there are any data to consider.
#set $recent=$span($day_delta=30, boundary='midnight')

<div id='hilo_widget' class="widget">
  <div class="widget_title">
    <a href="statistics.html">$gettext("Statistics")</a>
    <a class="widget_control"
      onclick="toggle_widget('hilo')">&diams;</a>
  </div>

  <div class="widget_contents">
  <table>
    <tbody>
      <tr>
        <td></td>
        <th>&nbsp;<br/>$gettext("Today")</th>
        <th class="hilo_week">&nbsp;<br/>$gettext("Week")</th>
        <th class="hilo_month">&nbsp;<br/>$gettext("Month")</th>
        <th class="hilo_year">&nbsp;<br/>$gettext("Year")</a></th>
        <td></td>
      </tr>

#for $x in $observations
  #if getattr($recent, $x).has_data
      <tr>
        <td class="label">$obs.label[$x]</td>
      #if $x.endswith('energy2') or $x.endswith('count')
        #for $timespan in $timespans
        <td class="data new_row hilo_$timespan">
          $getVar('%s.%s' % ($timespan, $x)).diff.format(add_label=False)</td>
        #end for
      #else
        #for $timespan in $timespans
        <td class="data new_row hilo_$timespan">
          <span title="$getVar('%s.%s' % ($timespan, $x)).maxtime">
            $getVar('%s.%s' % ($timespan, $x)).max.format(add_label=False)</span><br/>
          <span title="$getVar('%s.%s' % ($timespan, $x)).mintime">
            $getVar('%s.%s' % ($timespan, $x)).min.format(add_label=False)</span>
        </td>
        #end for
      #end if
        <td class="units">$getattr($unit.label, $x, '')</td>
      </tr>
  #end if
#end for

    </tbody>
  </table>
  </div>
</div>
