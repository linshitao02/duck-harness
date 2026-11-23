const { request } = require("../../utils/api");

Page({
  data: {
    base: "http://127.0.0.1:8787",
    username: "",
    password: "",
    error: "",
    busy: false,
  },
  onLoad() {
    const token = wx.getStorageSync("duck.token");
    const base = wx.getStorageSync("duck.base") || "http://127.0.0.1:8787";
    this.setData({ base });
    getApp().globalData.base = String(base).replace(/\/$/, "");
    if (token) wx.reLaunch({ url: "/pages/index/index" });
  },
  onBase(event) {
    this.setData({ base: event.detail.value });
  },
  onUsername(event) {
    this.setData({ username: event.detail.value });
  },
  onPassword(event) {
    this.setData({ password: event.detail.value });
  },
  async submit() {
    const base = String(this.data.base || "").trim().replace(/\/$/, "");
    const username = String(this.data.username || "").trim();
    const password = this.data.password || "";
    if (!base || !username || !password) {
      this.setData({ error: "服务器、用户名和密码都要填" });
      return;
    }
    this.setData({ busy: true, error: "" });
    wx.setStorageSync("duck.base", base);
    getApp().globalData.base = base;
    try {
      const data = await request("/api/auth/login", {
        method: "POST",
        data: { username, password, remember: true },
      });
      if (!data.token) throw new Error("服务器没有返回登录令牌");
      wx.setStorageSync("duck.token", data.token);
      wx.setStorageSync("duck.user", data.user && data.user.name ? data.user.name : username);
      wx.reLaunch({ url: "/pages/index/index" });
    } catch (error) {
      this.setData({ error: error.message || "登录失败" });
    } finally {
      this.setData({ busy: false });
    }
  },
});
