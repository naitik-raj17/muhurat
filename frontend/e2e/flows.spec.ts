import { expect, test } from '@playwright/test'
import { login, openFirstWedding } from './helpers.ts'

const PLANNER = 'planner@shubh-vivah.example'
const ADMIN = 'admin@shubh-vivah.example'
const VIEWER = 'viewer@shubh-vivah.example'

test('login rejects a wrong password', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('Email').fill(PLANNER)
  await page.getByLabel('Password').fill('wrong-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
})

test('planner searches vendors and shortlists one', async ({ page }) => {
  await login(page, PLANNER)
  const weddingId = await openFirstWedding(page)
  await page.goto(`/weddings/${weddingId}/vendors`)
  await page.getByLabel('Category').selectOption('photographer')
  await page.getByLabel('What are you looking for?').fill('candid wedding photography')
  await page.getByRole('button', { name: 'Search' }).click()
  const results = page.getByRole('region', { name: 'Search results' })
  const add = results.getByRole('button', { name: 'Add to shortlist' }).first()
  // The first search after an API restart loads the embedding model, which takes a while.
  await expect(add).toBeVisible({ timeout: 30_000 })
  await add.click()
  await expect(results.getByRole('button', { name: 'On shortlist' }).first()).toBeVisible()
})

test('planner cannot decide an out-of-limits approval; admin approves it', async ({ page }) => {
  await login(page, PLANNER)
  await page.goto('/approvals')
  const card = page.locator('article.approval').first()
  await expect(card).toBeVisible()
  await expect(card.getByRole('button', { name: 'Approve' })).toHaveCount(0)
  await expect(card).toContainText('approval:decide_override')

  await page.getByRole('button', { name: 'Sign out' }).click()
  await login(page, ADMIN)
  await page.goto('/approvals')
  const first = page.locator('article.approval').first()
  const summary = (await first.locator('strong').textContent())!
  const before = await page.locator('article.approval').count()
  await first.getByLabel('Decision note').fill('Checked with the couple')
  await first.getByRole('button', { name: 'Approve' }).click()
  await expect(page.locator('article.approval')).toHaveCount(before - 1)

  await page.getByLabel('Approval status').selectOption('approved')
  await expect(page.locator('article.approval', { hasText: summary }).first()).toContainText(
    'Checked with the couple',
  )
})

test('planner undoes a reversible agent action from the activity log', async ({ page }) => {
  await login(page, PLANNER)
  const weddingId = await openFirstWedding(page)
  await page.goto(`/weddings/${weddingId}/activity`)
  await page.getByRole('button', { name: 'Run agents now' }).click()
  await expect(page.getByRole('status')).toContainText('Agents finished')

  await page.getByLabel('Action status').selectOption('rolled_back')
  const undoneRows = page.locator('tbody tr')
  await expect(page.locator('table, p.muted').first()).toBeVisible()
  const undoneBefore = await undoneRows.count()

  await page.getByLabel('Action status').selectOption('executed')
  const undo = page.getByRole('button', { name: 'Undo' }).first()
  await expect(undo).toBeVisible()
  await undo.click()

  await page.getByLabel('Action status').selectOption('rolled_back')
  await expect(undoneRows).toHaveCount(undoneBefore + 1)
  await expect(undoneRows.first()).toContainText('AI')
})

test('viewer sees agent activity but cannot run agents or undo', async ({ page }) => {
  await login(page, VIEWER)
  await openFirstWedding(page)
  await page.getByRole('link', { name: 'Agent activity', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Agent activity' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run agents now' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Undo' })).toHaveCount(0)
})
