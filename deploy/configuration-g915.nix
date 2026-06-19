# STAGING — review then merge into /etc/nixos/configuration.nix and `sudo nixos-rebuild switch`. Not auto-applied.
#
# This file is NOT imported anywhere. It mirrors systemd/g915x-heatmap.service,
# systemd/g915x-heatmap-resume.service and the keyd block as the corrected NixOS
# expression. Copy the three blocks below into your existing configuration.nix
# (replacing the current g915-heatmap / keyd definitions), then rebuild.
#
# Assumes `g915-heatmap.py` sits next to your configuration.nix (the live system
# references it as ${./g915-heatmap.py}). Adjust the path if you keep it elsewhere.
{ config, pkgs, ... }:

{
  # =========================================================================
  # LOGITECH G915 X — TYPING HEATMAP (per-key RGB)
  # =========================================================================
  # Custom HID++ 2.0 driver (no Linux tool supports the c356 keyboard).
  # Reads keypresses from the keyboard's evdev node and paints each key
  # blue(cold) -> red(hot) by how often it is pressed, via HID++ feature 0x8081
  # per-key lighting on /dev/hidraw* (vendor node, auto-detected). Runs as root:
  # needs evdev read + hidraw write. Does NOT log keystrokes (press counts only).
  #   toggle:  systemctl stop g915-heatmap  /  systemctl start g915-heatmap
  #   logs:    journalctl -eu g915-heatmap
  systemd.services.g915-heatmap = {
    description = "Logitech G915 X typing heatmap (per-key RGB)";
    wantedBy = [ "multi-user.target" ];
    # after keyd so we read its virtual keyboard once keyd has grabbed the device
    after = [ "multi-user.target" "keyd.service" ];
    wants = [ "keyd.service" ];
    # self-polls for the device, so never let the start-limiter wedge it
    startLimitIntervalSec = 0;
    serviceConfig = {
      Type = "simple";
      ExecStart = "${pkgs.python3}/bin/python3 ${./g915-heatmap.py}";
      Restart = "always";
      RestartSec = "2s";

      # --- hardening: this daemon reads every keystroke as root; confine the
      # --- blast radius. Pure-stdlib Python, NO network sockets, reads
      # --- /dev/input/event* + /proc/bus/input/devices + /sys/class/hidraw/*,
      # --- writes ONLY to /dev/hidraw*. (uid drop is out of scope.)
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ProtectKernelModules = true;
      ProtectKernelTunables = true;
      ProtectControlGroups = true;
      ProtectClock = true;
      RestrictRealtime = true;
      RestrictNamespaces = true;
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      # no sockets at all
      RestrictAddressFamilies = "";
      IPAddressDeny = "any";
      SystemCallArchitectures = "native";
      SystemCallFilter = [ "@system-service" "~@privileged @resources" ];
      # deny all device nodes, then re-allow only the keyboard classes
      DevicePolicy = "closed";
      DeviceAllow = [ "char-hidraw rw" "char-input r" ];
    };
  };

  # Re-assert lighting after host resume: the keyboard drops its software
  # lighting state across sleep, so bounce the daemon when we wake.
  systemd.services.g915-heatmap-resume = {
    description = "Re-assert G915 X heatmap lighting after resume from sleep";
    wantedBy = [ "suspend.target" "hibernate.target" "hybrid-sleep.target" "suspend-then-hibernate.target" ];
    after = [ "suspend.target" "hibernate.target" "hybrid-sleep.target" "suspend-then-hibernate.target" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.systemd}/bin/systemctl --no-block restart g915-heatmap.service";
    };
  };

  # =========================================================================
  # G-KEY REMAPS — keyd (evdev-level, works on Wayland)
  # =========================================================================
  # The G-keys type as F13..F21 (G1..G9 = keycodes 183..191). keyd remaps them
  # below the display server. keyd *grabs* the keyboard and re-emits on
  # "keyd virtual keyboard", so g915-heatmap.py reads keyd's virtual device.
  #   add more binds: edit the [main] block; then  sudo nixos-rebuild switch
  services.keyd = {
    enable = true;
    keyboards.g915x = {
      # `k:` scopes to the keyboard interface. The bare "046d:c356" id also
      # matches the keyboard's phantom MOUSE interface, which keyd would then
      # grab too (breaking the mouse / spurious grabs) — `k:` avoids that.
      ids = [ "k:046d:c356" ];
      settings = {
        main = {
          # G5 -> Ctrl+Tab. Emits Ctrl+Tab and auto-repeats while held (this is
          # plain key auto-repeat, NOT a tap-vs-hold binding).
          f17 = "C-tab";
        };
      };
    };
  };
}
