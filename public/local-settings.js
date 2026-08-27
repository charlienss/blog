/* Qingyu'Blog · Python 本地设置覆盖层（自动生成） */
window.BLOG_LOCAL_SETTINGS = {
  "site_info": {},
  "profile": {},
  "nav": [],
  "moderate_comments": "0"
};
(function () {
  var s = window.BLOG_LOCAL_SETTINGS || {};
  var c = window.BLOG_CONFIG = window.BLOG_CONFIG || {};
  var site = s.site_info || {};
  var profile = s.profile || {};

  if (site.name) c.title = site.name;
  if (Object.prototype.hasOwnProperty.call(site, 'desc')) c.description = site.desc || '';
  if (Object.prototype.hasOwnProperty.call(site, 'avatar')) c.siteAvatar = site.avatar || '';
  c.profile = profile;

  if (Array.isArray(s.nav)) c.nav = s.nav;

  c.footer = Object.assign({}, c.footer || {});
  if (Object.prototype.hasOwnProperty.call(site, 'copyright')) c.footer.copyrightName = site.copyright || '';
  if (Object.prototype.hasOwnProperty.call(site, 'footerText')) c.footer.decl = site.footerText || '';

  c.moderateComments = String(s.moderate_comments || '0') === '1';

  try {
    if (site.name) document.title = site.name;
    var meta = document.querySelector('meta[name="description"]');
    if (meta && site.desc) meta.setAttribute('content', site.desc);
  } catch (e) {}
})();
