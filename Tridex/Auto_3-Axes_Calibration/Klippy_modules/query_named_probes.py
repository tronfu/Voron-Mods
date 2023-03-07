# Utility for querying the current state of all probes
# based on the same framework as query_endstops
#
# Copyright (C) 2023  Tron Fu <tron@riverwatcher.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
# Version: 1.0B1

import logging
import z_tilt
import named_probe

class QueryNamedProbes:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.probes = {}
        self.active_probe = None
        self.idex_probes = [None, None]
        self.last_state = []
        # Register webhook if server is available
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint(
            "query_probes/status", self._handle_web_request)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command("QUERY_PROBES", self.cmd_QUERY_PROBES,
                               desc=self.cmd_QUERY_PROBES_help)
        gcode.register_command("ACTIVATE_IDEX_PROBE", self.cmd_ACTIVATE_IDEX_PROBE,
                               desc=self.cmd_ACTIVATE_IDEX_PROBE_help)
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
    def _handle_connect(self):
        z_tilt = self.printer.lookup_object("z_tilt", None)
        if z_tilt != None:
            z_tilt.probe_helper = named_probe.NamedProbePointsHelper(z_tilt.probe_helper, z_tilt.probe_finalize)
    def register_probe(self, probe):
        self.probes[probe.name] = probe
        if probe.idex_carriage != None:
            self.idex_probes[probe.idex_carriage] = probe.name
        self.activate_probe(probe)
    def activate_probe_by_name(self, name):
        if name in self.probes:
            if self.is_probe_active(self.probes[name]):
                return
            self.probes[name].activate()
    def activate_probe(self, probe):
        if self.is_probe_active(probe):
            return
        if self.active_probe != None:
            self.probes[self.active_probe].deactivate()
        self.active_probe = probe.name
    def is_probe_active(self, probe):
        return self.active_probe == probe.name
    def get_active_probe(self):
        return self.probes[self.active_probe]
    def activate_idex_probe(self, carriage):
        if self.idex_probes[carriage] != None:
            self.activate_probe_by_name(self.idex_probes[carriage])
    def get_status(self, eventtime):
        return {'last_query': {name: value for name, value in self.last_state}}
    def _handle_web_request(self, web_request):
        gc_mutex = self.printer.lookup_object('gcode').get_mutex()
        toolhead = self.printer.lookup_object('toolhead')
        with gc_mutex:
            print_time = toolhead.get_last_move_time()
            self.last_state = [(name, mcu_probe.query_probe(print_time))
                               for mcu_endstop, name in self.endstops]
        web_request.send({name: ["open", "TRIGGERED"][not not t]
                          for name, t in self.last_state})
    def get_event_handlers_by_name(self, event):
        handlers = []
        for name, probe in self.probes.items():
            handlers.append(probe.get_event_handler_by_name(event))
        return handlers
    cmd_ACTIVATE_IDEX_PROBE_help = "ACTIVATE_IDEX_PROBE CARRIAGE=0|1"
    def cmd_ACTIVATE_IDEX_PROBE(self, gcmd):
        # Query the probes
        carriage = gcmd.get_int("CARRIAGE")
        if self.idex_probes[carriage] == None:
            gcmd.respond_info("No idex_carriage defined for named probes")
        else:
            self.activate_idex_probe(carriage)
            gcmd.respond_info("Probe %s on carriage %i activated" % (self.active_probe, carriage))
    cmd_QUERY_PROBES_help = "Report on the status of each probe"
    def cmd_QUERY_PROBES(self, gcmd):
        # Query the probes
        print_time = self.printer.lookup_object('toolhead').get_last_move_time()
        self.last_state = [(name, probe.mcu_probe.query_endstop(print_time))
                           for name, probe in self.probes.items()]
        # Report results
        msg = " ".join(["%s:%s" % (name, ["open", "TRIGGERED"][not not t])
                        for name, t in self.last_state])
        gcmd.respond_raw(msg)
def load_config(config):
    return QueryNamedProbes(config)
