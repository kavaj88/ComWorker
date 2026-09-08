const header = document.querySelector('[data-header]')
const navToggle = document.querySelector('.nav-toggle')
const siteNav = document.querySelector('.site-nav')

function setMenu(open) {
  if (!navToggle || !siteNav) return
  navToggle.setAttribute('aria-expanded', String(open))
  siteNav.classList.toggle('is-open', open)
  document.body.classList.toggle('nav-open', open)
}

navToggle?.addEventListener('click', () => {
  setMenu(navToggle.getAttribute('aria-expanded') !== 'true')
})

siteNav?.querySelectorAll('a').forEach((link) => {
  link.addEventListener('click', () => setMenu(false))
})

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') setMenu(false)
})

window.addEventListener(
  'scroll',
  () => header?.classList.toggle('is-scrolled', window.scrollY > 12),
  { passive: true },
)

const tabs = [...document.querySelectorAll('.view-tab')]
const panels = [...document.querySelectorAll('.view-panel')]

tabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    const selectedView = tab.dataset.view

    tabs.forEach((item) => {
      const active = item === tab
      item.classList.toggle('is-active', active)
      item.setAttribute('aria-selected', String(active))
    })

    panels.forEach((panel) => {
      const active = panel.dataset.panel === selectedView
      panel.classList.toggle('is-active', active)
      panel.hidden = !active
    })
  })
})

const revealItems = document.querySelectorAll('.reveal')
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

if (reduceMotion || !('IntersectionObserver' in window)) {
  revealItems.forEach((item) => item.classList.add('is-visible'))
} else {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return
        entry.target.classList.add('is-visible')
        observer.unobserve(entry.target)
      })
    },
    { threshold: 0.12, rootMargin: '0px 0px -40px' },
  )

  revealItems.forEach((item) => observer.observe(item))
}

window.addEventListener('resize', () => {
  if (window.innerWidth > 900) setMenu(false)
})
