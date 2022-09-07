The Brultech XTypes extension brultech.BTExtends.get_series() cannot do aggregation. This means the generic get_series()
gets called instead, with repeated invocations of get_aggregate() for every point in the series. If you have 32
channels, and are doing a week plot with 1 hour aggregation, that's 7 * 24 * 32 = 5,376 aggregation calls, each with at
least one database query.

Write a test program for the driver.
