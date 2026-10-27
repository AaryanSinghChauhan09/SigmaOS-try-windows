"""Try-before-install engine for SigmaOS-try / SigmaOS-try-windows
(inspired by try-omarchy, mint4win, try-omarchy-windows).

Principles: host capability probing is data in / pure checks out (self_test
supplies synthetic host facts -- no docker, qemu or /dev checks), backend
selection is declarative (`auto` walks container first, then VM, then fails
loudly), every plan is argv-only so it can be reviewed before execution.
"""
import argparse, json, os, re, shlex, sys

ENGINE = {
    "name": "sigma-try",
    "summary": "Try SigmaOS live: container/VM plans, requirements, launch",
    "language": "python",
    "verbs": ["container-plan", "vm-plan", "requirements", "launch-plan"],
    "principles": ["Separation of Concerns", "Declarative Planning",
                   "Defensive Programming", "Fail-Fast", "KISS",
                   "Deterministic Testing"],
}

cli_doc = """\
$ sigma-try requirements --host host.json --backend container
[ok  ] runtime: podman found
[ok  ] ram: 8192 MB
$ sigma-try container-plan --image ghcr.io/sigmaos/live:latest --runtime podman
$ sigma-try vm-plan /isos/sigmaos.iso --accel kvm --ram-mb 4096
$ sigma-try launch-plan --host host.json --backend auto
"""

DEFAULT_IMAGE = "ghcr.io/sigmaos/live:latest"
DEFAULT_ISO = "/isos/sigmaos-latest.iso"
_RUNTIMES = ("podman", "docker")
_ACCELS = ("kvm", "hvf", "tcg")
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_RAM_RE = re.compile(r"^(\d+)([bkmg])$", re.IGNORECASE)
_PORT_RE = re.compile(r"^(\d{1,5}):(\d{1,5})(?:/(tcp|udp))?$")


class TryError(ValueError):
    pass


def _one_line(value, what):
    if not isinstance(value, str) or not value.strip():
        raise TryError(f"empty {what}")
    if "\n" in value or "\x00" in value:
        raise TryError(f"{what} must be a single line")
    return value


def _int(value, what, lo, hi):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TryError(f"{what} must be an integer, got {value!r}")
    if not lo <= value <= hi:
        raise TryError(f"{what} must be {lo}..{hi}, got {value}")
    return value


# --------------------------------------------------------------------------- #
#  Container plan (pure argv)
# --------------------------------------------------------------------------- #
def container_plan(image=DEFAULT_IMAGE, name="sigmaos", runtime="podman",
                   ram="4g", cpus=4, ports=(), devices=(), gui=True,
                   cmdline=()):
    if runtime not in _RUNTIMES:
        raise TryError(f"unknown runtime {runtime!r} "
                       f"(have: {', '.join(_RUNTIMES)})")
    image = _one_line(image, "image")
    if not _IMAGE_RE.match(image):
        raise TryError(f"bad image reference: {image!r}")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise TryError(f"bad container name: {name!r}")
    if not isinstance(ram, str):
        raise TryError(f"bad memory: {ram!r}")
    m = _RAM_RE.match(ram.strip())
    if not m or int(m.group(1)) < 1:
        raise TryError(f"bad memory (e.g. 4g): {ram!r}")
    _int(cpus, "cpus", 1, 256)
    clean_ports = []
    for p in ports:
        pm = _PORT_RE.match(str(p)) if not isinstance(p, str) else _PORT_RE.match(p)
        if not pm or not (1 <= int(pm.group(1)) <= 65535
                          and 1 <= int(pm.group(2)) <= 65535):
            raise TryError(f"bad port mapping {p!r} (want host:container)")
        clean_ports.append(p)
    clean_devices = []
    for d in devices:
        d = _one_line(d, "device")
        if not d.startswith("/"):
            raise TryError(f"device path must be absolute: {d!r}")
        parts = d.split(":")
        if len(parts) > 2 or any(not p.startswith("/") for p in parts):
            raise TryError(f"bad device mapping: {d!r}")
        clean_devices.append(d)
    argv = [runtime, "run", "--rm", "-it", "--name", name,
            "--memory", ram, "--cpus", str(cpus)]
    for p in clean_ports:
        argv += ["-p", str(p)]
    for d in clean_devices:
        argv += ["-v", d]
    if gui:
        argv += ["--device", "/dev/dri", "--device", "/dev/input"]
    argv += ["--workdir", "/home/sigma", image]
    for c in cmdline:
        if not isinstance(c, str) or not c or "\n" in c or "\x00" in c:
            raise TryError(f"bad cmdline item: {c!r}")
        argv.append(c)
    return {"backend": "container", "runtime": runtime, "image": image,
            "name": name, "argv": argv}


# --------------------------------------------------------------------------- #
#  VM plan (pure argv steps)
# --------------------------------------------------------------------------- #
def vm_plan(iso=DEFAULT_ISO, ram_mb=4096, cpus=4, disk_gb=32, accel="kvm",
            ssh_port=2222, name="sigmaos-try"):
    iso = _one_line(iso, "iso")
    if not iso.endswith(".iso"):
        raise TryError(f"iso must end with .iso: {iso!r}")
    if accel not in _ACCELS:
        raise TryError(f"unknown accel {accel!r} (have: {', '.join(_ACCELS)})")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise TryError(f"bad vm name: {name!r}")
    _int(ram_mb, "ram_mb", 1024, 65536)
    _int(cpus, "cpus", 1, 64)
    _int(disk_gb, "disk_gb", 8, 2048)
    _int(ssh_port, "ssh_port", 1024, 65535)
    disk = iso[:-4] + ".qcow2"
    accel_flags = {"kvm": ["-enable-kvm", "-cpu", "host"],
                   "hvf": ["-accel", "hvf", "-cpu", "host"],
                   "tcg": ["-accel", "tcg"]}[accel]
    steps = [
        {"argv": ["qemu-img", "create", "-f", "qcow2", disk, f"{disk_gb}G"]},
        {"argv": ["qemu-system-x86_64"] + accel_flags + [
            "-m", str(ram_mb), "-smp", str(cpus), "-name", name,
            "-drive", f"file={disk},format=qcow2,if=virtio",
            "-cdrom", iso, "-boot", "d",
            "-nic", f"user,model=virtio-net-pci,hostfwd=tcp::{ssh_port}-:22"]},
    ]
    return {"backend": "vm", "iso": iso, "disk": disk, "accel": accel,
            "name": name, "steps": steps}


# --------------------------------------------------------------------------- #
#  Requirements: host facts (data in) -> checks (pure data out)
# --------------------------------------------------------------------------- #
def requirements(host, backend="container"):
    if not isinstance(host, dict):
        raise TryError("host facts must be a JSON object")
    if backend not in ("container", "vm"):
        raise TryError(f"unknown backend {backend!r} (container|vm|auto)")
    checks = []
    if backend == "container":
        rt = host.get("runtime")
        checks.append({"check": "runtime", "ok": rt in _RUNTIMES,
                       "detail": f"runtime={rt or 'none'}"})
        arch = host.get("arch")
        checks.append({"check": "arch", "ok": arch in ("x86_64", "aarch64"),
                       "detail": f"arch={arch or 'unknown'}"})
        ram = host.get("ram_mb", 0)
        checks.append({"check": "ram",
                       "ok": isinstance(ram, int) and ram >= 2048,
                       "detail": f"{ram} MB (min 2048)"})
    else:
        accel = host.get("accel")
        checks.append({"check": "accel", "ok": accel in _ACCELS,
                       "detail": f"accel={accel or 'none'}"})
        checks.append({"check": "qemu", "ok": bool(host.get("qemu")),
                       "detail": "qemu present" if host.get("qemu")
                       else "qemu missing"})
        ram = host.get("ram_mb", 0)
        checks.append({"check": "ram",
                       "ok": isinstance(ram, int) and ram >= 4096,
                       "detail": f"{ram} MB (min 4096)"})
        disk = host.get("disk_gb", 0)
        checks.append({"check": "disk",
                       "ok": isinstance(disk, int) and disk >= 20,
                       "detail": f"{disk} GB free (min 20)"})
    return checks


def all_ok(checks):
    return all(c["ok"] for c in checks)


def render_checks(checks, backend="container"):
    lines = []
    for c in checks:
        mark = "ok  " if c["ok"] else "FAIL"
        lines.append(f"[{mark}] {c['check']}: {c['detail']}")
    failed = [c["check"] for c in checks if not c["ok"]]
    if failed:
        lines.append(f"{backend}: blocked by {', '.join(failed)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
#  Backend selection + launch plan (still pure argv)
# --------------------------------------------------------------------------- #
def launch_plan(host, backend="auto", image=DEFAULT_IMAGE, iso=DEFAULT_ISO,
                name="sigmaos"):
    """Pick a backend from host facts and return its plan. Fail-fast."""
    if backend not in ("auto", "container", "vm"):
        raise TryError(f"unknown backend {backend!r} (auto|container|vm)")

    def _blocked(b):
        failed = [c["check"] for c in requirements(host, b) if not c["ok"]]
        return f"{b} unavailable: {', '.join(failed)}"

    chosen = backend
    if backend == "auto":
        if all_ok(requirements(host, "container")):
            chosen = "container"
        elif all_ok(requirements(host, "vm")):
            chosen = "vm"
        else:
            raise TryError("host meets neither backend -- "
                           + _blocked("container") + "; " + _blocked("vm"))
    elif not all_ok(requirements(host, backend)):
        raise TryError(_blocked(backend))

    if chosen == "container":
        plan = container_plan(image=image, name=name,
                              runtime=host.get("runtime") or "podman",
                              ram="4g", cpus=max(1, int(host.get("cpus", 2))),
                              gui=True)
    else:
        plan = vm_plan(iso=iso, accel=host.get("accel") or "tcg",
                       ram_mb=max(1024, int(host.get("ram_mb", 4096))),
                       cpus=max(1, int(host.get("cpus", 2))),
                       disk_gb=max(8, int(host.get("disk_gb", 32))),
                       name=f"{name}-vm")
    return {"backend": chosen, "plan": plan}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=ENGINE["name"], description=ENGINE["summary"])
    sub = p.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("container-plan", help="build the podman/docker argv")
    cp.add_argument("--image", default=DEFAULT_IMAGE)
    cp.add_argument("--name", default="sigmaos")
    cp.add_argument("--runtime", default="podman", choices=_RUNTIMES)
    cp.add_argument("--ram", default="4g")
    cp.add_argument("--cpus", type=int, default=4)
    cp.add_argument("--port", action="append", default=[], dest="ports")
    cp.add_argument("--device", action="append", default=[], dest="devices")
    cp.add_argument("--headless", action="store_true")
    cp.add_argument("cmdline", nargs="*")
    vp = sub.add_parser("vm-plan", help="build the qemu argv steps")
    vp.add_argument("--iso", default=DEFAULT_ISO)
    vp.add_argument("--ram-mb", type=int, default=4096)
    vp.add_argument("--cpus", type=int, default=4)
    vp.add_argument("--disk-gb", type=int, default=32)
    vp.add_argument("--accel", default="kvm", choices=_ACCELS)
    vp.add_argument("--ssh-port", type=int, default=2222)
    vp.add_argument("--name", default="sigmaos-try")
    rq = sub.add_parser("requirements", help="evaluate host facts")
    rq.add_argument("--host", required=True, help="JSON host facts file")
    rq.add_argument("--backend", default="container",
                    choices=("container", "vm"))
    lp = sub.add_parser("launch-plan", help="pick a backend and print its plan")
    lp.add_argument("--host", required=True)
    lp.add_argument("--backend", default="auto",
                    choices=("auto", "container", "vm"))
    lp.add_argument("--image", default=DEFAULT_IMAGE)
    lp.add_argument("--iso", default=DEFAULT_ISO)
    lp.add_argument("--name", default="sigmaos")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "container-plan":
            plan = container_plan(args.image, args.name, args.runtime,
                                  args.ram, args.cpus, args.ports,
                                  args.devices, not args.headless,
                                  args.cmdline)
            print(shlex.join(plan["argv"]))
        elif args.cmd == "vm-plan":
            plan = vm_plan(args.iso, args.ram_mb, args.cpus, args.disk_gb,
                           args.accel, args.ssh_port, args.name)
            print(f"# disk image: {plan['disk']}")
            for step in plan["steps"]:
                print(shlex.join(step["argv"]))
        else:
            with open(args.host, "r", encoding="utf-8") as fh:
                host = json.load(fh)
            if args.cmd == "requirements":
                checks = requirements(host, args.backend)
                print(render_checks(checks, args.backend), end="")
                if not all_ok(checks):
                    return 1
            else:  # launch-plan
                plan = launch_plan(host, args.backend, args.image, args.iso,
                                   args.name)
                print(f"# backend: {plan['backend']}")
                if plan["backend"] == "container":
                    print(shlex.join(plan["plan"]["argv"]))
                else:
                    for step in plan["plan"]["steps"]:
                        print(shlex.join(step["argv"]))
        return 0
    except (TryError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def self_test() -> None:
    # --- container plan (pure argv) -----------------------------------------
    plan = container_plan("ghcr.io/sigmaos/live:latest", "sigmaos", "podman",
                          "4g", 4, ports=("8080:80",),
                          devices=("/home/u/share:/data",), gui=True,
                          cmdline=["nvim", "README.md"])
    assert plan["argv"] == [
        "podman", "run", "--rm", "-it", "--name", "sigmaos",
        "--memory", "4g", "--cpus", "4",
        "-p", "8080:80", "-v", "/home/u/share:/data",
        "--device", "/dev/dri", "--device", "/dev/input",
        "--workdir", "/home/sigma", "ghcr.io/sigmaos/live:latest",
        "nvim", "README.md"], plan["argv"]
    docker = container_plan(runtime="docker", gui=False, name="mint4win")
    assert docker["argv"][0] == "docker"
    assert "--device" not in docker["argv"]           # headless: no dri
    assert container_plan() == container_plan()       # deterministic
    for bad_call in ((container_plan, (DEFAULT_IMAGE, "SigmaOS")),   # uppercase
                     (container_plan, (DEFAULT_IMAGE, "")),
                     (container_plan, ("bad image ref",)),
                     (container_plan, ("",)),
                     (container_plan, (DEFAULT_IMAGE, "ok", "runc")),
                     (container_plan, (DEFAULT_IMAGE, "ok", "podman", "4x")),
                     (container_plan, (DEFAULT_IMAGE, "ok", "podman", "0g")),
                     (container_plan, (DEFAULT_IMAGE, "ok", "podman", "g")),
                     (container_plan, (DEFAULT_IMAGE, "ok", "podman", "4g", 0)),
                     (container_plan, (DEFAULT_IMAGE, "ok", "podman", "4g", 300)),
                     (container_plan, (DEFAULT_IMAGE, "ok", "podman", "4g", "4")),
                     (container_plan, (DEFAULT_IMAGE, "ok", "podman", "4g", 4,
                                       ("99999:80",))),
                     (container_plan, (DEFAULT_IMAGE, "ok", "podman", "4g", 4,
                                       ("http:80",))),
                     (container_plan, (DEFAULT_IMAGE, "ok", "podman", "4g", 4,
                                       (), ("relative/path",))),
                     (container_plan, (DEFAULT_IMAGE, "ok", "podman", "4g", 4,
                                       (), (), True, ("bad\narg",)))):
        try:
            bad_call[0](*bad_call[1])
        except TryError:
            pass
        else:
            raise AssertionError(f"should have rejected: {bad_call}")

    # --- vm plan (pure argv steps) ------------------------------------------
    vm = vm_plan("/isos/sigmaos.iso", ram_mb=4096, cpus=4, disk_gb=32,
                 accel="kvm", ssh_port=2222, name="sigmaos-try")
    assert vm["disk"] == "/isos/sigmaos.qcow2"
    assert vm["steps"][0]["argv"] == ["qemu-img", "create", "-f", "qcow2",
                                      "/isos/sigmaos.qcow2", "32G"]
    qemu = vm["steps"][1]["argv"]
    assert qemu[0] == "qemu-system-x86_64"
    assert qemu[1:4] == ["-enable-kvm", "-cpu", "host"]
    assert qemu[qemu.index("-m") + 1] == "4096"
    assert qemu[qemu.index("-cdrom") + 1] == "/isos/sigmaos.iso"
    assert "hostfwd=tcp::2222-:22" in qemu[qemu.index("-nic") + 1]
    tcg = vm_plan(accel="tcg")["steps"][1]["argv"]
    assert tcg[1:3] == ["-accel", "tcg"] and "-enable-kvm" not in tcg
    for bad_call in ((vm_plan, ("/isos/disk.img",)),      # not an iso
                     (vm_plan, ("",)),
                     (vm_plan, (DEFAULT_ISO, 512)),       # ram too small
                     (vm_plan, (DEFAULT_ISO, 4096, 0)),   # no cpus
                     (vm_plan, (DEFAULT_ISO, 4096, 4, 4)),  # tiny disk
                     (vm_plan, (DEFAULT_ISO, 4096, 4, 32, "xen")),
                     (vm_plan, (DEFAULT_ISO, 4096, 4, 32, "kvm", 80)),  # priv port
                     (vm_plan, (DEFAULT_ISO, 4096, 4, 32, "kvm", 2222,
                                "Bad Name"))):
        try:
            bad_call[0](*bad_call[1])
        except TryError:
            pass
        else:
            raise AssertionError(f"should have rejected: {bad_call}")
    assert vm_plan() == vm_plan()                       # deterministic

    # --- requirements (pure over synthetic host facts) -----------------------
    good_c = {"runtime": "podman", "arch": "x86_64", "ram_mb": 8192}
    checks = requirements(good_c, "container")
    assert [c["check"] for c in checks] == ["runtime", "arch", "ram"]
    assert all_ok(checks) is True
    no_rt = requirements({**good_c, "runtime": None}, "container")
    assert no_rt[0]["ok"] is False and no_rt[1]["ok"] is True
    small = requirements({**good_c, "ram_mb": 1024}, "container")
    assert small[2]["ok"] is False and "min 2048" in small[2]["detail"]
    weird_arch = requirements({**good_c, "arch": "armv7l"}, "container")
    assert weird_arch[1]["ok"] is False
    good_vm = {"accel": "kvm", "qemu": True, "ram_mb": 8192, "disk_gb": 64,
               "arch": "x86_64"}
    vm_checks = requirements(good_vm, "vm")
    assert [c["check"] for c in vm_checks] == ["accel", "qemu", "ram", "disk"]
    assert all_ok(vm_checks) is True
    weak_vm = requirements({"accel": None, "qemu": False, "ram_mb": 2048,
                            "disk_gb": 16}, "vm")
    assert [c["ok"] for c in weak_vm] == [False, False, False, False]
    text = render_checks(no_rt, "container")
    assert "[FAIL] runtime" in text and "blocked by runtime" in text
    assert render_checks(checks, "container").endswith("ram: 8192 MB (min 2048)\n")
    for bad_call in ((requirements, ("facts.txt", "container")),
                     (requirements, (good_c, "bare-metal")),
                     (requirements, (good_c, "auto"))):
        try:
            bad_call[0](*bad_call[1])
        except TryError:
            pass
        else:
            raise AssertionError(f"should have rejected: {bad_call}")

    # --- backend selection ---------------------------------------------------
    auto_c = launch_plan(good_c, "auto")
    assert auto_c["backend"] == "container"
    assert auto_c["plan"]["argv"][0] == "podman"
    auto_docker = launch_plan({**good_c, "runtime": "docker"}, "auto")
    assert auto_docker["plan"]["argv"][0] == "docker"
    auto_vm = launch_plan({**good_c, "runtime": None, "accel": "kvm",
                           "qemu": True, "disk_gb": 64}, "auto")
    assert auto_vm["backend"] == "vm"
    assert auto_vm["plan"]["steps"][1]["argv"][0] == "qemu-system-x86_64"
    explicit = launch_plan(good_c, "container")
    assert explicit["backend"] == "container"
    for host, backend, why in (
            ({**good_c, "runtime": None, "arch": "armv7l", "ram_mb": 512,
              "accel": None, "qemu": False, "disk_gb": 8}, "auto",
             "neither"),
            (good_c, "vm", "accel"),                  # no kvm on this host
            ({**good_c, "runtime": None}, "container", "runtime")):
        try:
            launch_plan(host, backend)
        except TryError as e:
            msg = str(e)
            if why == "neither":
                assert "neither backend" in msg, msg
            else:
                assert why in msg, (why, msg)
        else:
            raise AssertionError(f"should have rejected host/backend {why}")
    try:
        launch_plan(good_c, "bare-metal")
    except TryError:
        pass
    else:
        raise AssertionError("unknown backend not rejected")
    # launch plans are argv-only data: no subprocess anywhere
    assert isinstance(auto_c["plan"]["argv"], list)
    assert all(isinstance(a, str) for a in auto_c["plan"]["argv"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
