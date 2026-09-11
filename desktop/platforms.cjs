const platforms = {
  boss: {
    name: "BOSS 直聘",
    domain: "zhipin.com",
    login: "https://www.zhipin.com/web/user/?ka=header-login",
    jobs: "https://www.zhipin.com/web/geek/jobs",
  },
  zhaopin: {
    name: "智联招聘",
    domain: "zhaopin.com",
    login: "https://passport.zhaopin.com/login",
    jobs: "https://www.zhaopin.com/jobs/?pageMode=search",
  },
};

function platformForURL(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return (
      Object.keys(platforms).find((key) => {
        const domain = platforms[key].domain;
        return url.hostname === domain || url.hostname.endsWith("." + domain);
      }) || null
    );
  } catch {
    return null;
  }
}

module.exports = { platforms, platformForURL };
