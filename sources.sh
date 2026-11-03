#!/bin/sh
# Start the two local database mocks the Drift tab reads (no model is loaded, so no lock):
#   NetBox mock      http://127.0.0.1:3303   (server/mocks/mock/netbox.js)
#   ServiceNow mock  http://127.0.0.1:3304   (server/mocks/mock/servicenow.js)
# Both serve the ground truths in server/mocks/ground_truth (vdo1 is the office rack SP-HYD-RM01-R01-R1,
# from data/ground_truth/SP-HYD-RM01-R01-R1/netbox_devices.csv). Stop them with: kill $(lsof -ti TCP:3303) $(lsof -ti TCP:3304)

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/server/mocks" || exit 1
node mock/netbox.js 3303 &
node mock/servicenow.js 3304 &
wait
