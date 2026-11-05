# Changelog

## 0.7.1

- fix: normalize release notes and scope commit checks (#54) ([57bab87](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/57bab87dd7171e22d21d4464b8c84922908224d2))
- fix: compact idle printer statistics (#52) ([cdcd4b0](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/cdcd4b0103aad3274beb101a5d05bccf899cf799))
- fix: refresh release notes after publication (#53) ([52f3e29](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/52f3e29a8c0bb1e608c28e61245f390fe50c3ce3))

## 0.7.0

- feat: show four toolheads as filament index tabs (#49) ([2348f4b](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/2348f4b19ce46c61b07c9b05ab0f66717f91fc9b))
- feat: show filament details and add U1 toolhead SVGs (#48) ([aec0e21](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/aec0e21d16491c29f63d56806033b7d9c4943e1d))
- fix: clarify stream status and printer feedback (#46) ([fa08506](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/fa08506cca5ce25f7836179e6ba2b4f3b8fb1161))

## 0.6.0

- fix: renew camera monitoring every sixty seconds (#44) ([1ef5e7c](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/1ef5e7ce7f2f461a3945eb2f8d4a422d1f6ba7eb))
- fix: wait for camera acknowledgement until the deadline (#43) ([6cd2ab4](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/6cd2ab4c18a74c5271efc45bc748565c0eb73dc6))
- feat: clarify temperature targets and streamline preview controls (#42) ([b650b54](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/b650b544ecfa7a701ed3d478ab15365af7186d33))
- chore: format staged files and align uv cooldown settings (#41) ([7c07e4f](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/7c07e4fad71593f14b9c387e90a86c5882bf10e5))
- fix: clarify print progress layout (#40) ([9bd0e1e](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/9bd0e1e33b4f11a6e87d263de57a89dbc80e67c6))
- build: streamline visual review and isolate release checks (#39) ([9bd24ce](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/9bd24ce0232bfd77a4029591b348b48fe2d22562))
- fix: retain the last camera frame during snapshot failures (#37) ([aace94b](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/aace94bb1c0f8c60279c179a65cb4cc01f299c91))
- fix: align print estimates with slicer progress and preparation (#36) ([cbb8db8](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/cbb8db85ac7c07d2518665a64fcdab0384d6f6ee))

## 0.5.0

- fix: keep release suggestions free of PR attribution links (#33) ([3e0eed4](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/3e0eed48d5a0ade9da12ca08c64cd83fefceba33))
- feat: show the U1 logo in Orca's native tab (#31) ([809e022](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/809e022ecdf054bf35c4f407ea42014581116ecb))
- feat: suggest release notes from source PR summaries (#32) ([c6c6aac](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/c6c6aac99296c97fc1de759f7f4b98e0524d529b))
- fix: make release-note review visible and require fresh confirmation (#30) ([1b94ad5](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/1b94ad53270809dd36de76e2f77be066d02b0fd0))
- fix: keep setup logs out of GitHub release outputs (#28) ([2b32fad](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/2b32fad1143aa99ed1227b1e93414d786cb64f17))
- refactor: migrate camera UI to Svelte with pnpm (#27) ([963c234](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/963c2347a885871f389eb79e67d059713eabc018))

## 0.4.0

- feat: add camera stats and public release notes (#24) ([669857b](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/669857b3b062f16424efd16a31b28e7141acb1c1))

## 0.3.6

- fix: honor revocation of remembered preset access (#21) ([064919e](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/064919ea6e488b56acaaa5a9700a201694fc33d5))

## 0.3.5

- docs: replace app icon with a transparent vector render (#19) ([ae1cedb](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/ae1cedb859ec9e15ccf0a344f019faf1a26ab113))
- fix(ci): publish to Orca Cloud from release events (#17) ([6da6fde](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/6da6fdefd061662e201f6ee5fd93bb549d43929d))

## 0.3.4

- fix: identify the Cloud publisher to avoid Cloudflare rejection (#14) ([25cc0ee](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/25cc0eef8b14351ab285ae13f84e147a5145eb48))

## 0.3.3

- ci: publish GitHub release assets to Orca Cloud (#11) ([3bcf58a](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/3bcf58a87c062fd64fb1a76300ae3c147d3b802e))

## 0.3.2

- fix: require consent before reading preset connection settings (#9) ([774985e](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/774985eef5b3832dfa3d399c3d2a0a292a262d09))
- ci: fix snapshot check lookup and skip full tests for version bumps (#7) ([4327e67](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/4327e67f9942b42b46f89859ec261a274b9c77a3))

## 0.3.1

- docs: add approved U1 branding and README logo (#5) ([c9d119e](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/c9d119e07315a2be4f1d04ca14af8468f24538cb))
- build: name universal plugin artifacts for Orca Cloud ([198b9df](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/198b9dfdcbf47032218b910c68412133fb5e90d1))
- ci: merge snapshot version PRs after successful checks ([9de77f5](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/9de77f58654288dbc58d1a7b37f5c28968e2f52c))

## 0.3.0

- deps: bump websocket-client from 1.9.0 to 1.9.2 (#1) ([1e46616](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/1e46616bc0b2e4aaa3d817eb9f07065cf645777f))
- feat: add Snapmaker U1 camera plugin ([eca6735](https://github.com/lanwen/orca-snapmaker-u1-camera-plugin/commit/eca6735042afa8b946e24315c55b361cdafd4506))

