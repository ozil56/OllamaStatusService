'use strict';
// Shared by /manage and /dashboard. A display-only cookie never changes server
// settings, authentication or active jobs. Reloading leaves server-owned work intact.
(() => {
  const options = [['auto','🌐 Auto'],['zh-CN','简体中文'],['zh-TW','繁體中文'],['en','English'],['ja','日本語'],['ko','한국어'],['de','Deutsch'],['fr','Français'],['es','Español']];
  options[0][1] = ({'zh-CN':'自动（浏览器语言）','zh-TW':'自動（瀏覽器語言）',en:'Auto (browser)',ja:'自動（ブラウザー）',ko:'자동 (브라우저)',de:'Automatisch (Browser)',fr:'Auto (navigateur)',es:'Auto (navegador)'})[document.documentElement.lang] || 'Auto (browser)';
  const selected = new URL(location.href).searchParams.get('lang') || decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('oss.language='))?.split('=')[1] || 'auto');
  for (const picker of document.querySelectorAll('[data-language-picker]')) {
    picker.replaceChildren(...options.map(([value,label])=>{const item=document.createElement('option');item.value=value;item.textContent=label;return item;}));
    picker.value=options.some(x=>x[0]===selected)?selected:'auto';
    picker.addEventListener('change',()=>{
      document.cookie='oss.language='+encodeURIComponent(picker.value)+'; Path=/; Max-Age=31536000; SameSite=Lax';
      const url=new URL(location.href);url.searchParams.delete('lang');location.replace(url);
    });
  }
})();
