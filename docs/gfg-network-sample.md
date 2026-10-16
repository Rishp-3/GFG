# Captured GFG requests (verified 2026-10-16, logged-in website)

No cookies or tokens are recorded here.

## 1. Solved problems (profile)
`POST https://practiceapi.geeksforgeeks.org/api/v1/user/problems/submissions/`
Body: `{"handle":"rishp_3","requestType":"","year":"","month":""}`
Response: `{"status":"success","count":149,"result":{"Easy":{"700889":{"slug":"reverse-an-array","pname":"Reverse Array","lang":"java","user_subtime":"2025-12-12 02:44:26"}}}}`
Groups: School, Basic, Easy, Medium, Hard.

## 2. Submissions of one problem
`GET https://practiceapi.geeksforgeeks.org/api/latest/problems/<slug>/submissions/user/?`
Response: `{"results":{"id":700292,"slug":"generate-ip-addresses","submissions":[{"submission_id":"e3fdb457-...","exec_status_text":"Correct","exec_status":"1","lang":"java","subtime":"2026-03-13 23:33:32","pid":"700292", ...}]}}`

## 3. Code of one submission
`GET https://practiceapi.geeksforgeeks.org/api/latest/problems/submissions/<submission_id>/?`
Response: `{"code":"","status":"1","language":"java","user_code":"import java.util.*;\n..."}`

## Not verified
Pagination of request 2 for problems with very many submissions; a
problem-description endpoint; whether the cookie is required for request 1.
