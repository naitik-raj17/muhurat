import { expect, type Page } from '@playwright/test'

export const PASSWORD = 'Password123!'

export async function login(page: Page, email: string): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  // After a sign-out the app returns to the page you were on, so only check we left /login.
  await expect(page).not.toHaveURL(/\/login$/)
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
}

/** Open the first wedding card and return its id from the URL. */
export async function openFirstWedding(page: Page): Promise<string> {
  await page.goto('/weddings')
  await page.locator('a.wedding-card').first().click()
  await expect(page).toHaveURL(/\/weddings\/[0-9a-f-]{36}$/)
  return page.url().split('/').at(-1)!
}
