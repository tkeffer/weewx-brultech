## statistics module for weewx skins
## Copyright Tom Keffer, Matthew Wall
## See LICENSE.txt for your rights
#errorCatcher Echo
#encoding UTF-8

#set $time_tags = [$day, $week, $month, $year]

## Get the list of observations from the configuration file, otherwise fallback
## to a very rudimentary set of observations.
#set $observations = $DisplayOptions.get('observations_stats', ['ch1_a_energy2'])

## use this span to determine whether there are any data to consider.
#set $recent=$span($day_delta=30, boundary='midnight')

<div id="statistics_widget" class="widget">
  <div class="widget_title">
    $gettext("Statistics")
  </div>
  <div class="widget_contents">

    <table>
      <tbody>
        <tr>
          <td></td>
          <td></td>
          <th>$gettext("Today")</th>
          <th class="hilo_week">$gettext("Week")</th>
          <th class="hilo_month">$gettext("Month")</th>
          <th class="hilo_year">$gettext("Year")</th>
        </tr>

#for $x in $observations
  #if getattr($recent, $x).has_data
        <tr>
          <td class="label">$obs.label[$x]</td>
          <td class="units">$getattr($unit.label, $x, '')</td>
      #if $x.endswith('energy2') or $x.endswith('count')
        #for $time_tag in $time_tags
          <td class="data new_row">$getattr($time_tag, $x).diff.format(add_label=False)</td>
        #end for
      #else
        #for $time_tag in $time_tags
          <td class="data new_row">
            $getattr($time_tag, $x).max.format(add_label=False)<br/>
            <span class="timestamp">$getattr($time_tag, $x).maxtime</span><br/>
            $getattr($time_tag, $x).min.format(add_label=False)<br/>
            <span class="timestamp">$getattr($time_tag, $x).mintime</span>
          </td>
        #end for
      #end if
        </tr>
  #end if
#end for

      </tbody>
    </table>

  </div>
</div>
