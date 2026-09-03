// Stub for @shoelace-style/localize
// WebAwesome loader imports this; MarketHub uses English-only defaults.

export class LocalizeController {
  constructor(host) {
    this.host = host;
    this.lang = "en";
  }
  hostConnected() {}
  hostDisconnected() {}
  update() {}
  translate(key, ...args) {
    return key;
  }
}

const _translations = {};
export function registerTranslation(langCode, translation) {
  _translations[langCode] = translation;
}
export default { LocalizeController, registerTranslation };
