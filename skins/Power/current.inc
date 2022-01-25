## current module for weewx skins
## Copyright Tom Keffer, Matthew Wall
## See LICENSE.txt for your rights
#errorCatcher Echo
#encoding UTF-8

<div id='current_widget' class="widget">
  <div class="widget_title">
    $gettext("Current Conditions")
    <a class="widget_control"
      onclick="toggle_widget('current')">&diams;</a>
  </div>

  <div class="widget_contents">
  <table>
    <tbody>

#if 'observations_current' in $DisplayOptions
#for $x in $DisplayOptions.observations_current
      <tr>
        <td class="label">$obs.label[$x]</td>
    #if $x.endswith('energy2') or $x.endswith('count')
        <td class="data">$getVar('day.' + $x).diff</td>
    #else
        <td class="data">$getVar('current.' + $x)</td>
    #end if
      </tr>
#end for
#else
    <tr><td>No observations specified</td></tr>
#end if

    </tbody>
  </table>
  </div>

</div>
