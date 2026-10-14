# Your own AvethyOS

Keep everything in AvethyOS and add your own packages and settings. GitHub builds
your image for you. You do not need to build Linux or install build tools.

An **image** is the complete operating system downloaded by your computer.
Your GitHub **repository** stores your choices. Its **workflow** builds those
choices into your own image. You choose one graphics version; it never builds two.

## Start with a fork, using the GitHub website

1. Open [avethyos-personal](https://github.com/5aether/avethyos-personal) and click
   **Fork**. This small repository is separate from the main operating system.
2. Open **Actions** and enable workflows for your fork if GitHub asks.
3. Open `image.conf`, click the pencil button, choose your graphics version and
   save using **Commit changes**:

   ```ini
   # AMD or Intel graphics:
   VARIANT=standard

   # Replace the line above with this for NVIDIA graphics:
   # VARIANT=nvidia

   IMAGE_NAME=avethyos-personal
   ```

   Keep exactly one `VARIANT=` line. A laptop with both integrated graphics and
   NVIDIA graphics should use `nvidia`.
4. Saving your changes on your fork's default branch starts the build. Open
   **Actions → Build my AvethyOS** to follow it. A green check means your image
   and installer are ready. Open that run's summary to find its exact address,
   such as `ghcr.io/yourname/avethyos-personal:latest`. To build again without
   changing files, use **Run workflow** and select **force**.

`IMAGE_NAME` is only the short package name: `avethyos-personal`. Do not put
`ghcr.io/yourname/avethyos-personal` in that setting. The workflow adds your GitHub
owner name and the `ghcr.io/` prefix automatically.

The separate `5aether/avethyos-personal` repository builds its own image and ISO,
and forks build theirs in their own namespace. Only the default branch publishes.
Your fork includes the build workflow; you do not need the main AvethyOS sources.
Edit files in the repository whose image you use. A fork has its own files;
changing the source repository does
not automatically change an existing fork.

## Or clone into a new repository, including a private one

Create an empty repository on GitHub, with no initial README or license. It can
be public or private. In a terminal, run:

```bash
git clone https://github.com/5aether/avethyos-personal my-avethyos
cd my-avethyos
git remote rename origin upstream
git remote add origin https://github.com/YOURNAME/YOURREPO.git
git push -u origin main
```

Replace `YOURNAME/YOURREPO` with the address of your new repository. GitHub must
already be connected to your Git client; account passwords cannot authenticate
Git pushes. [GitHub's setup guide](https://docs.github.com/en/get-started/git-basics/set-up-git)
explains connecting your account.

Keep `main` as the default branch, enable Actions, and follow steps 3–4 above.
Your editable files are directly at the repository's root. No special branch,
extra folder or source-code copy is needed.

## Add Fedora packages

Edit `packages.txt` and write one Fedora package name on each line:

```text
# Comments start with #.
gimp
vlc
```

Save the file on your default branch. GitHub rebuilds automatically, using the
latest successful AvethyOS release. `remove-packages.txt` works the same way for
packages you want to remove. Leave it empty to keep all existing features.

Packages such as Blueman need a folder for their saved settings. The build adds
the required startup rules automatically when these packages are installed, so
their folders also exist after system updates. Existing saved settings are kept.

Removing a name from `packages.txt` removes your added package on the next build;
each build starts again from AvethyOS. Software installed separately through
Homebrew or Distrobox remains separately managed. Flatpak choices have their own
files, explained below.

## Add or remove Flatpak apps

Edit `flatpak-packages.txt` and put one **Application ID** on each line. Find the
ID on the application's page at [Flathub](https://flathub.org). For example:

```text
# Web browser:
org.mozilla.firefox
# Video player:
org.videolan.VLC
```

Use the ID, not the app's display name or a terminal command. Empty lines and
comments starting with `#` are ignored. Apps use Flathub's stable release.
Save the file with **Commit changes** on your default branch. This starts your
image build and, after it succeeds, your installer ISO build.

The build checks that these apps install successfully and includes apps and
dependencies that Flatpak supports in the image's offline cache. Some apps,
including Spotify, download extra files from their publisher. Flatpak cannot
export these apps offline: they need an internet connection for their first
installation. The build detects them automatically; keep their IDs in the same
list. [Flatpak's offline export rules](https://github.com/flatpak/flatpak/blob/main/app/flatpak-builtins-create-usb.c)
explain this restriction.

Apps are installed for everyone on the first boot of a new installation, and
new choices are applied on the next boot after a system update. Connect to the
internet for apps that need publisher downloads. They appear in Quickshell's
app launcher. App updates remain available through Bazaar or `flatpak update`.

To remove an app supplied by AvethyOS, put its ID in
`remove-flatpak-packages.txt`. For example:

```text
# Remove the permissions editor supplied by AvethyOS:
com.github.tchx84.Flatseal
```

Removing a name from `flatpak-packages.txt` also removes that previously
image-provided app after your next update and boot. Apps you installed separately
and their saved data remain under your control. Removing an app does not delete
its saved data. Leave both files empty to keep AvethyOS's original apps.

If you uninstall an image-provided app yourself, Flatpak remembers your choice
and does not reinstall it on every boot. You can reinstall it in Bazaar whenever
you want. This uses Flatpak's supported
[operating system app defaults](https://docs.flatpak.org/en/latest/flatpak-command-reference.html#flatpak-preinstall).

## Change kernel settings

Edit `kernel-arguments.toml`. For example, to make an Apple keyboard's top row
act as ordinary function keys:

```toml
kargs = ["hid_apple.fnmode=2"]
```

For several settings, put each one in quotes and separate them with commas.
Use `kargs = []` to clear your personal image's arguments. Bootc applies these
image settings when switching or upgrading; they take effect after reboot.
Inherited AvethyOS arguments stay managed by AvethyOS. Image-owned arguments
should be changed in the image, as explained in the
[bootc kernel argument documentation](https://bootc.dev/bootc/building/bootc-kernel-arguments.7.html).

Other kernel settings can be ordinary files. For example,
`files/usr/lib/sysctl.d/90-personal.conf` supplies sysctl defaults and
`files/usr/lib/modprobe.d/personal.conf` supplies driver options.

## Add files or advanced build commands

Put files under `files/`, with the same path they should have in the image.
For example, `files/usr/share/backgrounds/my-wallpaper.png` becomes
`/usr/share/backgrounds/my-wallpaper.png`. Keep shared system defaults under
`usr/` where possible. Existing user configuration and `/etc` changes are
preserved by system updates and may override new image defaults.

`build.sh` is an optional place for commands that run as root **inside the build**.
It runs after package installation and file copying. For example:

```bash
systemctl enable my-service.service
```

Your image keeps AvethyOS's existing layers and adds a small customization layer.
There is no second full distribution build or separate NVIDIA build.

## Download your installer ISO

Each successful new image build automatically creates an installer for exactly
the graphics version in `image.conf`. The installer contains your packages,
kernel settings and copied files from that same build. It follows your own
`:latest` image for future updates.

Open **Actions → Build my AvethyOS**, select the completed run, and scroll to
**Artifacts**. Download the item named after your image and graphics version.
Unzip it to get the `.iso` file and its `.iso.sha256` checksum. Write the ISO to
a USB drive using Fedora Media Writer, then boot the USB and choose your disk,
account and password in the installer. The installer leaves these choices to you.

Artifacts stay available for 14 days. Use **Run workflow → force** to create a
fresh image and installer when an old download expires. A failed image build
or an unchanged upstream check creates no ISO. Private repository downloads
require access to that repository; registry credentials are never included in
the ISO.

## Use your image on an AvethyOS computer

After the first green build, replace the example address with the one in your
workflow summary:

```bash
sudo bootc switch ghcr.io/YOURNAME/avethyos-personal:latest
```

Reboot when you are ready. Your files and applications in your home directory
remain in place. Future system updates use your own image:

```bash
sudo bootc upgrade
```

Reboot when you want to start the downloaded update. AvethyOS stages automatic
updates quietly and does not restart your computer for you.

## Private repositories and private images

The GitHub workflow uses its own short-lived `GITHUB_TOKEN` to publish to GHCR,
GitHub's image registry. No registry password belongs in this repository or image.
If a package already exists, open its **Package settings → Manage Actions access**
and give your repository write access. Your first published package is normally
private; you can change its visibility in package settings if you want public
downloads. A private repository and a public image are separate choices.

For a private image, your computer needs a persistent download credential before
the first `bootc switch`. Create a GitHub **personal access token (classic)** with
`read:packages`, from an account that can read your image. Authorize it for your
organization's SSO if required. Then run:

```bash
avethyos image login YOURNAME
```

At the token prompt, paste the token. The prompt hides what you type.
The command saves it in `/etc/ostree/auth.json`, readable only by root.
Bootc reads this file for later upgrades, including automatic upgrades and after
reboots. An ordinary `podman login` without this file does not configure bootc's
persistent credentials. Renew the login when your token expires. See
[bootc credentials](https://bootc.dev/bootc/building/bootc-secrets.7.html) and
[GitHub registry authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## How updates follow AvethyOS

The official build is scheduled daily at **09:00 Europe/Rome**, including daylight
saving changes. Your build waits for a completed, successful release of **both**
official variants, then downloads only the variant you chose. It checks the
release's source commit and uses its exact image digest, so a failed build,
pull-request build or changing `latest` tag cannot become your base.

Your repository checks every five minutes. It does no image build when both your
files and the selected upstream digest are unchanged. Changes to your own files
also trigger a check. The first build waits for a successful release from the
current **Build and verify AvethyOS** workflow; older releases cannot pass these
checks. **Run workflow → force** rebuilds unchanged choices when needed.

For immediate builds, the AvethyOS maintainer can register your repository:

- Upstream variable **PERSONAL_IMAGE_REPOS**: whitespace-separated `OWNER/REPO` names.
- Upstream secret **PERSONAL_IMAGE_DISPATCH_TOKEN**: a fine-grained token or GitHub
  App token with **Contents: write**, limited to those destination repositories.

The separate notification workflow runs only after the entire official release
succeeds. Each receiver verifies that release again. A failed notification cannot
undo the official release; scheduled checks still recover it.

Private repositories use your GitHub Actions allowance even for checks that skip
an image build. After immediate notifications are configured, you can turn off
polling by adding your repository's Actions variable **POLL_UPSTREAM=false** under
**Settings → Secrets and variables → Actions → Variables**. File changes and
manual runs still work. Keep polling enabled if you want it as a fallback.

GitHub can delay or drop scheduled jobs, and it disables schedules in inactive
public repositories after 60 days. A GitHub schedule cannot guarantee an exact
start time. These are
[GitHub's schedule limits](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Check your changes locally

GitHub automatically checks package lists, kernel arguments and release planning
on every push and pull request. If you have Python 3.11 or later and Bash, you can
run the same checks locally without building or changing your operating system:

```bash
python3 -B -m unittest discover -s tests -v
python3 -B -c 'from scripts.upstream import configuration, check_customization; configuration(); check_customization()'
bash -n customize.sh build.sh scripts/build-iso.sh scripts/rpm-state.sh tests/check-rpm-state.sh
```

## Optional signing

Signing is not required for a working personal image or a private repository.
Advanced users can add an existing Cosign private key as the repository Actions
secret **SIGNING_SECRET**, and its password as **SIGNING_PASSWORD** if encrypted.
The workflow signs the exact published digest before updating `latest`. Configuring
signature enforcement on your computer also requires your public key and a
matching container policy. GitHub artifact attestations are not required.
