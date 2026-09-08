const { chromium } = require('playwright')

const target = 'http://127.0.0.1:4173/'
const outputRoot = 'C:/Users/avajy/AppData/Local/Temp'

async function inspectPage(page, width, height, name) {
  const errors = []
  page.on('pageerror', (error) => errors.push(`page: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`)
  })

  await page.setViewportSize({ width, height })
  await page.goto(target, { waitUntil: 'networkidle' })
  const visibleImages = page.locator('img:visible')
  await visibleImages.first().waitFor({ state: 'visible', timeout: 10000 })
  const brokenImages = await visibleImages.evaluateAll((images) =>
    images.filter((image) => !image.complete || image.naturalWidth === 0).map((image) => image.src),
  )
  if (brokenImages.length) errors.push(`broken images: ${brokenImages.join(', ')}`)

  const layout = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    title: document.querySelector('h1')?.textContent?.replace(/\s+/g, ' ').trim(),
    sections: document.querySelectorAll('main > section').length,
  }))

  if (layout.scrollWidth > layout.clientWidth + 1) {
    errors.push(`horizontal overflow: ${layout.scrollWidth} > ${layout.clientWidth}`)
  }

  await page.evaluate(() => {
    document.querySelectorAll('.reveal').forEach((item) => item.classList.add('is-visible'))
    window.scrollTo(0, 0)
  })
  await page.waitForTimeout(100)

  await page.screenshot({ path: `${outputRoot}/comworker-site-${name}.png`, fullPage: true })
  await page.locator('#contact').screenshot({ path: `${outputRoot}/comworker-contact-${name}.png` })
  return { name, layout, errors }
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    args: ['--disable-gpu', '--no-sandbox'],
  })
  const desktopPage = await browser.newPage()
  const desktop = await inspectPage(desktopPage, 1440, 900, 'desktop')

  await desktopPage.locator('[data-view="client"]').click()
  const clientPanelVisible = await desktopPage.locator('[data-panel="client"]').isVisible()
  const adminPanelVisible = await desktopPage.locator('[data-panel="admin"]').isVisible()
  if (!clientPanelVisible || adminPanelVisible) desktop.errors.push('product view tabs did not switch panels')

  const mobilePage = await browser.newPage()
  const mobile = await inspectPage(mobilePage, 390, 844, 'mobile')
  await mobilePage.locator('.nav-toggle').click()
  const mobileNavVisible = await mobilePage.locator('#site-nav').isVisible()
  const mobileNavExpanded = await mobilePage.locator('.nav-toggle').getAttribute('aria-expanded')
  if (!mobileNavVisible || mobileNavExpanded !== 'true') mobile.errors.push('mobile menu did not open')

  await browser.close()
  const report = { desktop, mobile }
  console.log(JSON.stringify(report, null, 2))
  if (desktop.errors.length || mobile.errors.length) process.exit(1)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
