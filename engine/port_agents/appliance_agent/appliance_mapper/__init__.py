"""appliance_mapper - the router mapper's pipeline for firewalls, load balancers and gateways.

    from appliance_mapper.pipeline import map_appliance
    result = map_appliance("rack.jpg")                                  # make, model and type read off the photo
    result = map_appliance("rack.jpg", "Fortinet", "FG-200F", out_dir="out/", device="firewall")
"""
