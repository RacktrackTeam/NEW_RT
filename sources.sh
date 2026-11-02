#!/bin/sh
# Start the two local database mocks the Drift tab reads (no model is loaded, so no lock):
#   NetBox mock      http://127.0.0.1:3301   (modules/sources/mock/netbox.js)
#   ServiceNow mock  http://127.0.0.1:5301   (modules/sources/mock/servicenow.js)
# Both serve the ground truths in modules/sources/ground_truth (vdo1 is the office rack SP-HYD-RM01-R01-R1,
# from data/ground_truth/SP-HYD-RM01-R01-R1/netbox_devices.csv). Stop them with: kill $(lsof -ti TCP:3301) $(lsof -ti TCP:5301)

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/modules/sources" || exit 1
node mock/netbox.js 3301 &
node mock/servicenow.js 5301 &
wait
