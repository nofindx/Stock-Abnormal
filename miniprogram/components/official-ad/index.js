const { client } = require('../../utils/ads-config')
const { supported } = require('../../utils/rewarded-access')

Component({
  properties: { placement: String, allowed: { type: Boolean, value: false } },
  data: { mounted: false, loaded: false, failed: false, unitId: '', foreground: true },
  observers: { 'allowed, placement': function() { if (this._attached) this.checkConfig() } },
  lifetimes: {
    attached() { this._attached = true; this._attempted = false; this.checkConfig() },
    detached() { this._attached = false; this._sequence = (this._sequence || 0) + 1 }
  },
  pageLifetimes: {
    show() { this.setData({ foreground: true }); this.checkConfig() },
    hide() { this.setData({ foreground: false }) }
  },
  methods: {
    async checkConfig() {
      const sequence = this._sequence = (this._sequence || 0) + 1
      if (!this._attached || !this.properties.allowed || !this.data.foreground) return
      const slot = await client.placement(this.properties.placement)
      if (!this._attached || sequence !== this._sequence || !this.properties.allowed || !this.data.foreground) return
      this._slotEnabled = Boolean(slot && supported(wx, [2, 10, 4]))
      if (this.data.mounted && slot && slot.unitId !== this.data.unitId) this._slotEnabled = false
      if (!this._slotEnabled) { this.setData({ failed: true }); return }
      if (this._attempted) {
        if (this.data.loaded && !this._adError) this.setData({ failed: false })
        return
      }
      this._attempted = true
      this.setData({ mounted: true, unitId: slot.unitId, failed: false })
    },
    onAdLoad() {
      if (this._attached && this._slotEnabled) { this._adError = false; this.setData({ loaded: true, failed: false }) }
    },
    onAdError() {
      if (this._attached) { this._adError = true; this.setData({ loaded: false, failed: true }) }
    }
  }
})
