# Creating the First Super Admin

SuperAdmin accounts are **never** created through self-registration or any
API endpoint — there is no UI, and no route, that can set
`users.is_super_admin = true`. This is intentional: it's the one privilege
level the backend will not let any authenticated user (including other
admins) grant.

The first SuperAdmin must therefore be created manually, directly in the
database.

## Steps

1. Create the account the normal way first — either through the public
   registration form (role = **Admin**, if you've enabled admin
   self-registration) or via the existing `POST /api/admin/users` endpoint
   as another admin. Either way it starts out as a regular admin account.

2. In the **Supabase Dashboard → SQL Editor**, run:

   ```sql
   update users
   set is_super_admin = true,
       status = 'approved'
   where username = '<the account's username>'
     and role = 'admin';
   ```

   (`status = 'approved'` is only needed if the account came in through
   self-registration and is still pending — a SuperAdmin obviously
   shouldn't be stuck waiting on itself for approval.)

3. Confirm it took effect:

   ```sql
   select id, username, email, role, is_super_admin, status
   from users
   where is_super_admin = true;
   ```

That's the entire process. There is no seed script or startup flag that
does this automatically — it's a deliberate, one-time, human action so a
SuperAdmin can never be minted by application code alone.

## What SuperAdmin unlocks

Once `is_super_admin = true`, the account can, on top of everything a
regular admin can do:

- Approve or reject **Admin** self-registrations (`POST
  /api/admin/pending-requests/{id}/approve` / `/reject`) — a regular admin
  gets a `403` if they try this on an admin-role request.
- Change a user's `role` via `PUT /api/admin/users/{uid}` (e.g. promote an
  existing user to `admin`) — a regular admin gets a `403` on this field.

Creating **additional** SuperAdmins isn't exposed through the API either —
repeat the SQL step above for any further accounts you want to elevate.
