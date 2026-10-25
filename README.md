    # SigmaOS-try-windows

    Try SigmaOS from Windows (WSL/boot)

    Part of the **SigmaOS** sovereign operating-system ecosystem — a multi-repository
    distribution modelled on Linux Mint and Omarchy Linux. See the parent
    [`SigmaOS`](https://github.com/AaryanSinghChauhan09/SigmaOS) repository for the
    full component index.

    ## Upstream inspirations
    - https://github.com/linuxmint/mint4win
- https://github.com/omacom/try-omarchy-windows

    ## Design principles applied
    - **Separation of Concerns**
- **Declarative Planning**
- **Defensive Programming**
- **Fail-Fast**
- **KISS**
- **Deterministic Testing**

    ## Usage
    Verbs: `container-plan, vm-plan, requirements, launch-plan`

    ```
    $ sigma-try requirements --host host.json --backend container
[ok  ] runtime: podman found
[ok  ] ram: 8192 MB
$ sigma-try container-plan --image ghcr.io/sigmaos/live:latest --runtime podman
$ sigma-try vm-plan /isos/sigmaos.iso --accel kvm --ram-mb 4096
$ sigma-try launch-plan --host host.json --backend auto

    ```

    ## Build & test
    ```
    make build
    make test
    ```

    ## Licence
    GNU General Public License v3.0 (FOSS).
