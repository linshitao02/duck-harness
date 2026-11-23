const { request } = require("../../utils/api");

Page({
  data: {
    items: [],
    userName: "用户",
    loading: true,
  },
  onShow() {
    if (!wx.getStorageSync("duck.token")) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    this.setData({ userName: wx.getStorageSync("duck.user") || "用户" });
    this.load();
  },
  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh());
  },
  async load() {
    this.setData({ loading: true });
    try {
      const data = await request("/api/conversations");
      const items = (data.items || []).filter((item) => !item.hidden);
      this.setData({ items });
    } catch (error) {
      if (error.message === "请先登录") {
        wx.reLaunch({ url: "/pages/login/login" });
        return;
      }
      wx.showToast({ title: error.message || "加载失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },
  open(event) {
    const id = event.currentTarget.dataset.id;
    const title = event.currentTarget.dataset.title || "会话";
    wx.navigateTo({ url: "/pages/chat/chat?id=" + id + "&title=" + encodeURIComponent(title) });
  },
  async create() {
    try {
      const data = await request("/api/conversations", {
        method: "POST",
        data: { title: "新会话" },
      });
      wx.navigateTo({ url: "/pages/chat/chat?id=" + data.id + "&title=" + encodeURIComponent(data.title || "新会话") });
    } catch (error) {
      wx.showToast({ title: error.message || "没能新建", icon: "none" });
    }
  },
  async logout() {
    try {
      await request("/api/auth/logout", { method: "POST", data: {} });
    } catch (error) {
      // 令牌已经失效时，本地清掉即可。
    }
    wx.removeStorageSync("duck.token");
    wx.reLaunch({ url: "/pages/login/login" });
  },
});
