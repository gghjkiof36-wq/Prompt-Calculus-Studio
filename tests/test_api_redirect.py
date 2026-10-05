"""Exercise the API transport's redirect handler with synthetic credentials."""
import io,unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request
from urllib.response import addinfourl
from email.message import Message
import urllib.request as transport
from prompt_calculus_studio import civitai


class ApiRedirectTests(unittest.TestCase):
    def test_actual_api_opener_follows_redirect_policy_for_all_redirect_codes(self):
        for code in (301,302,303,307,308):
            for target,auth in [('https://elsewhere.invalid/next',None),('https://civitai.red:444/next',None),('http://civitai.red/next',None),('https://civitai.red:443/next','Bearer synthetic')]:
                with self.subTest(code=code,target=target):
                    seen=[]
                    def respond(handler,request):
                        seen.append((request.full_url,request.get_header('Authorization')));headers=Message()
                        if len(seen)==1:headers['Location']=target
                        response=addinfourl(io.BytesIO(b'{}'),headers,request.full_url,code if len(seen)==1 else 200);response.msg='Fixture';return response
                    with patch.object(transport.HTTPSHandler,'https_open',respond),patch.object(transport.HTTPHandler,'http_open',respond):
                        try:civitai.CivitAIClient('synthetic').test_connection()
                        except civitai.CivitAIError:
                            self.assertTrue(target.startswith('http:'))
                    self.assertEqual(seen[0][1],'Bearer synthetic')
                    if target.startswith('http:'):self.assertEqual(len(seen),1)
                    else:self.assertEqual(seen[1][1],auth)

    def test_api_uses_origin_policy_and_never_legacy_urlopen(self):
        for target,expected in [('https://other.invalid/api',None),('https://civitai.red:444/api',None),
                                ('https://civitai.red:443/api','Bearer synthetic'),('https://civitai.red/api','Bearer synthetic')]:
            seen=[]
            def opener(handler):
                def open_request(req,timeout):
                    redirected=handler.redirect_request(req,None,302,'Found',{},target)
                    seen.append(redirected.get_header('Authorization'));return io.BytesIO(b'{}')
                return SimpleNamespace(open=open_request)
            with patch.object(civitai,'build_opener',opener),patch.object(civitai,'urlopen',side_effect=AssertionError('Unsafe transport')):
                civitai.CivitAIClient('synthetic').test_connection()
            self.assertEqual(seen,[expected])

    def test_downgrade_is_rejected_before_any_request_and_auth_does_not_return_on_chain(self):
        handler=civitai._SafeAuthRedirect(); request=Request('https://civitai.red/api',headers={'Authorization':'Bearer synthetic'})
        with self.assertRaises(civitai.CivitAIError):handler.redirect_request(request,None,302,'Found',{},'http://civitai.red/api')
        first=handler.redirect_request(request,None,302,'Found',{},'https://cdn.invalid/file')
        back=handler.redirect_request(first,None,302,'Found',{},'https://civitai.red/back')
        self.assertIsNone(back.get_header('Authorization'))
        self.assertEqual(request.get_header('Authorization'),'Bearer synthetic')
