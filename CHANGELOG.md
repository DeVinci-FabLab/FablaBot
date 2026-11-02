# Changelog

## [0.6.0](https://github.com/DeVinci-FabLab/FablaBot/compare/v0.5.0...v0.6.0) (2026-11-02)


### ⚠ BREAKING CHANGES

* **docker:** run the bot as an unprivileged user in a hardened container

### Features

* **docker:** run the bot as an unprivileged user in a hardened container ([b3b7a05](https://github.com/DeVinci-FabLab/FablaBot/commit/b3b7a05eee707fa1fd295b9a3772843911e101da))

## [0.5.0](https://github.com/DeVinci-FabLab/FablaBot/compare/v0.4.0...v0.5.0) (2026-11-02)


### Features

* **access:** let each allowed server carry a name after its ID ([#136](https://github.com/DeVinci-FabLab/FablaBot/issues/136)) ([0eb4b89](https://github.com/DeVinci-FabLab/FablaBot/commit/0eb4b896364ba5e39e3da9fc289f6c78ce24193a))


### Bug Fixes

* **storage:** keep the reaction history for three years ([#137](https://github.com/DeVinci-FabLab/FablaBot/issues/137)) ([6ce97cf](https://github.com/DeVinci-FabLab/FablaBot/commit/6ce97cf74f3ad1fda19e917ea5d5eeb02afe1b49))


### Documentation

* state the automatic update and the move without sudo ([#134](https://github.com/DeVinci-FabLab/FablaBot/issues/134)) ([bd92d1b](https://github.com/DeVinci-FabLab/FablaBot/commit/bd92d1bebc9ab4d23b7b6996fa214c4551f7d175))

## [0.4.0](https://github.com/DeVinci-FabLab/FablaBot/compare/v0.3.0...v0.4.0) (2026-11-02)


### Features

* **storage:** move the bot's state to SQLite, with its import and rollback ([c97d0b6](https://github.com/DeVinci-FabLab/FablaBot/commit/c97d0b689b94b83ad56fe0aeb7e7d7465a3d0d70))


### Documentation

* say a release can deploy itself and archive before merging it ([#130](https://github.com/DeVinci-FabLab/FablaBot/issues/130)) ([1807f7d](https://github.com/DeVinci-FabLab/FablaBot/commit/1807f7d323f5e200b915214f95ec0910e9cf1819))

## [0.3.0](https://github.com/DeVinci-FabLab/FablaBot/compare/v0.2.0...v0.3.0) (2026-11-01)


### Features

* **startup:** report what each server lacks at start ([#126](https://github.com/DeVinci-FabLab/FablaBot/issues/126)) ([8226c24](https://github.com/DeVinci-FabLab/FablaBot/commit/8226c24918955f7198d1fd1bcbce1b339badd2ac))


### Bug Fixes

* **startup:** remove leftover server commands at start ([#128](https://github.com/DeVinci-FabLab/FablaBot/issues/128)) ([c4915e7](https://github.com/DeVinci-FabLab/FablaBot/commit/c4915e714b7f24964925542531874b3aaea6e482))
* **user:** refuse giving a role at or above your own ([#129](https://github.com/DeVinci-FabLab/FablaBot/issues/129)) ([3b73b9b](https://github.com/DeVinci-FabLab/FablaBot/commit/3b73b9b531e04c0fe7efc43f2af6bd87234de7ab))

## [0.2.0](https://github.com/DeVinci-FabLab/FablaBot/compare/v0.1.0...v0.2.0) (2026-10-31)


### Features

* **access:** serve only the servers in an allow-list ([#101](https://github.com/DeVinci-FabLab/FablaBot/issues/101)) ([383dfc9](https://github.com/DeVinci-FabLab/FablaBot/commit/383dfc9afcdffff4e1d773c7dfea2fe34ba93794))
* add easter egg toggle ([3f3e1ec](https://github.com/DeVinci-FabLab/FablaBot/commit/3f3e1ec8af6f3b1ee8e92bce4848b44f8e8dceaf))
* **commands:** hide staff command groups from members in the slash menu ([#110](https://github.com/DeVinci-FabLab/FablaBot/issues/110)) ([ccf0c73](https://github.com/DeVinci-FabLab/FablaBot/commit/ccf0c73b0af534fdfadde737ae607ebc6343b907))
* **commands:** sync slash commands only when they change ([#108](https://github.com/DeVinci-FabLab/FablaBot/issues/108)) ([460ce6d](https://github.com/DeVinci-FabLab/FablaBot/commit/460ce6dc7809004d9bddff0de421e0b5d518027e))
* **fm:** show formation dates as Discord timestamps ([#123](https://github.com/DeVinci-FabLab/FablaBot/issues/123)) ([cfbf8e1](https://github.com/DeVinci-FabLab/FablaBot/commit/cfbf8e16b9078db565fca3901a865ec79c750149))
* **help:** add /help listing only what the member can run, in en, fr and de ([#118](https://github.com/DeVinci-FabLab/FablaBot/issues/118)) ([c25ccbd](https://github.com/DeVinci-FabLab/FablaBot/commit/c25ccbd2d98589d84ad8b33cd931475fcef1ad33))
* **vocal:** let Bureau create temporary voice channels ([#120](https://github.com/DeVinci-FabLab/FablaBot/issues/120)) ([bf11a28](https://github.com/DeVinci-FabLab/FablaBot/commit/bf11a288645fe792cd13e280ac65e2ca739b1a70))


### Bug Fixes

* **access:** make refusals say what to do next ([#104](https://github.com/DeVinci-FabLab/FablaBot/issues/104)) ([00f0e26](https://github.com/DeVinci-FabLab/FablaBot/commit/00f0e26f762ffa03e5a99cbcf9b9ebac01839a20))
* clear the five type errors in src ([#119](https://github.com/DeVinci-FabLab/FablaBot/issues/119)) ([4bd99a7](https://github.com/DeVinci-FabLab/FablaBot/commit/4bd99a72054179fd1268d5ad7cbb3600b361d7bf))
* **fm:** refuse a formation starting in the past and stop acting on stale state ([#122](https://github.com/DeVinci-FabLab/FablaBot/issues/122)) ([21b6f1a](https://github.com/DeVinci-FabLab/FablaBot/commit/21b6f1a6c0493189d821c7096e6c9b87aeea5c0a))
* **help:** let members run a private /help in any channel ([#125](https://github.com/DeVinci-FabLab/FablaBot/issues/125)) ([608f2db](https://github.com/DeVinci-FabLab/FablaBot/commit/608f2dbc5dd818e327053befc00a2e1b7d6cc7e7))
* keep secrets and dev tools out of the bot image ([#100](https://github.com/DeVinci-FabLab/FablaBot/issues/100)) ([7c70f65](https://github.com/DeVinci-FabLab/FablaBot/commit/7c70f6539426a9d0f9e5d78021702250c3a8b453))
* keep suggestion authors and message bodies out of the logs ([#97](https://github.com/DeVinci-FabLab/FablaBot/issues/97)) ([16287d3](https://github.com/DeVinci-FabLab/FablaBot/commit/16287d3d0b6c378538983b606d2d3eaab4fd455e))
* **logging:** give each server its own log channel and log export ([#102](https://github.com/DeVinci-FabLab/FablaBot/issues/102)) ([e9dcdba](https://github.com/DeVinci-FabLab/FablaBot/commit/e9dcdbadeff9a865139ef2081b01b9e2d8bfb618))
* remove the offensive easter-egg triggers and the hard-coded guild id ([#99](https://github.com/DeVinci-FabLab/FablaBot/issues/99)) ([bfe969f](https://github.com/DeVinci-FabLab/FablaBot/commit/bfe969fb06493b183dcdbed2c60331a9ba0240d2))
* stop temporary admins from escalating and end every op on time ([#98](https://github.com/DeVinci-FabLab/FablaBot/issues/98)) ([a6c7508](https://github.com/DeVinci-FabLab/FablaBot/commit/a6c7508bc739eea83a1456c30022f7ec62b130dc))
* **suggest:** keep the chosen recipient selected after toggling anonymity ([#121](https://github.com/DeVinci-FabLab/FablaBot/issues/121)) ([22db26d](https://github.com/DeVinci-FabLab/FablaBot/commit/22db26d4d4de2f8e542c433a79020c5199290339))


### Documentation

* add an operator guide for running an instance ([#103](https://github.com/DeVinci-FabLab/FablaBot/issues/103)) ([5729a97](https://github.com/DeVinci-FabLab/FablaBot/commit/5729a97819baa682e34e89ad58179af18b412fe9))
* complete the operator guide for releases, command sync and staff command visibility ([#115](https://github.com/DeVinci-FabLab/FablaBot/issues/115)) ([c2bfbb2](https://github.com/DeVinci-FabLab/FablaBot/commit/c2bfbb23b5bddeb091b81770eab47649565a6550))
