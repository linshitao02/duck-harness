App({
  globalData: {
    base: "http://127.0.0.1:8787",
  },
  onLaunch() {
    const base = wx.getStorageSync("duck.base");
    if (base) this.globalData.base = String(base).replace(/\/$/, "");
  },
});
