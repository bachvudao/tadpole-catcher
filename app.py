import os
import re
import sys
import pdb
import time
import shutil
import pickle
import logging
import logging.config

from random import randrange
from getpass import getpass
from os.path import abspath, dirname, join, isfile, isdir

import requests
import lxml.html
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from xvfbwrapper import Xvfb

# -----------------------------------------------------------------------------
# Logging stuff
# -----------------------------------------------------------------------------
logging_config = dict(
    version = 1,
    formatters = {
        'f': {'format':
              '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'}
        },
    handlers = {
        'h': {'class': 'logging.StreamHandler',
              'formatter': 'f',
              'level': logging.DEBUG}
        ,
        'f': {'class': 'logging.FileHandler',
              'formatter': 'f',
              'filename': 'logs/tadpole.log',
              'level': logging.INFO}
    },      
    root = {
        'handlers': ['h', 'f'],
        'level': logging.DEBUG,
        },
)

logging.config.dictConfig(logging_config)

# -----------------------------------------------------------------------------
# The scraper code.
# -----------------------------------------------------------------------------
class DownloadError(Exception):
    pass


class Client:

    COOKIE_FILE = "state/cookies.pkl"
    ROOT_URL = "http://tadpoles.com/"
    HOME_URL = "https://www.tadpoles.com/parents"
    MIN_SLEEP = 1
    MAX_SLEEP = 3

    def __init__(self):
        self.init_logging()

    def init_logging(self):
        logger = logging.getLogger('tadpole-catcher')
        self.info = logger.info
        self.debug = logger.debug
        self.warning = logger.warning
        self.critical = logger.critical
        self.exception = logger.exception

    def __enter__(self):
        self.info("Starting xvfb display")
        self.vdisplay = Xvfb()
        self.vdisplay.start()
        self.info("Starting browser")
        self.br = self.browser = webdriver.Firefox()
        self.br.implicitly_wait(10)
        self.info("Got a browser")
        return self

    def __exit__(self, *args):
        self.info("Shutting down browser")
        self.browser.quit()
        self.info("Shutting down xfvb display")
        self.vdisplay.stop()

    def sleep(self, minsleep=None, maxsleep=None):
        _min = minsleep or self.MIN_SLEEP
        _max = maxsleep or self.MAX_SLEEP
        duration = randrange(_min * 100, _max * 100) / 100.0
        self.info('Sleeping %r' % duration)
        time.sleep(duration)

    def navigate_url(self, url):
        self.info("Navigating to %r" % url)
        self.br.get(url)

    def load_cookies(self):
        self.info("Loading cookies.")
        if not isdir('state'):
            os.mkdir('state')
        with open(self.COOKIE_FILE, "rb") as f:
            self.cookies = pickle.load(f)

    def dump_cookies(self):
        self.info("Dumping cookies.")
        self.cookies = self.br.get_cookies()
        with open(self.COOKIE_FILE,"wb") as f:
            pickle.dump(self.br.get_cookies(), f)

    def add_cookies_to_browser(self):
        self.info("Adding the cookies to the browser.")
        for cookie in self.cookies:
            if self.br.current_url.strip('/').endswith(cookie['domain']):
                self.br.add_cookie(cookie)

    def requestify_cookies(self):
        # Cookies in the form requests expects.
        self.info("Transforming the cookies for requests lib.")
        self.req_cookies = {}
        for s_cookie in self.cookies:
            self.req_cookies[s_cookie["name"]] = s_cookie["value"]

    def switch_windows(self):
        '''Switch to the other window.'''
        self.info("Switching windows.")
        all_windows = set(self.br.window_handles)
        current_window = set([self.br.current_window_handle])
        other_window = (all_windows - current_window).pop()
        self.br.switch_to.window(other_window)

    def do_login(self):
        # Navigate to login page.
        self.info("Navigating to login page.")
        self.br.find_element_by_id("login-button").click()
        self.br.find_element_by_class_name("tp-block-half").click()
        self.br.find_element_by_class_name("other-login-button").click()

        # Focus on the google auth popup.
        self.switch_windows()

        # Enter email.
        email = self.br.find_element_by_id("Email")
        email.send_keys(raw_input("Enter email: "))
        email.submit()

        # Enter password.
        passwd = self.br.find_element_by_id("Passwd")
        passwd.send_keys(getpass("Enter password:"))
        passwd.submit()

        # Enter 2FA pin.
        #pin = self.br.find_element_by_id("totpPin")
        #pin.send_keys(getpass("Enter google verification code: "))
        #pin.submit()

        # wait while users approve through google mobile phone app
        raw_input("Enter a key when you have approved on mobile phone")    

        # Click "approve".
        self.info("Sleeping 2 seconds.")
        self.sleep(minsleep=2)
        self.info("Clicking 'approve' button.")
        self.br.find_element_by_id("submit_approve_access").click()

        # Switch back to tadpoles.
        self.switch_windows()

    def iter_monthyear(self):
        '''Yields pairs of xpaths for each year/month tile on the
        right hand side of the user's home page.
        '''
        month_xpath_tmpl = '//*[@id="app"]/div[4]/div[1]/ul/li[%d]/div/div/div/div/span[%d]'
        month_index = 1
        while True:
            month_xpath = month_xpath_tmpl % (month_index, 1)
            year_xpath = month_xpath_tmpl % (month_index, 2)

            # Go home if not there already.
            if self.br.current_url != self.HOME_URL:
                self.navigate_url(self.HOME_URL)
            try:
                # Find the next month and year elements.
                month = self.br.find_element_by_xpath(month_xpath)
                year = self.br.find_element_by_xpath(year_xpath)
            except NoSuchElementException:
                # We reached the end of months on the profile page.
                self.info("No months left to scrape. Stopping.")
                sys.exit(0)

            self.month = month
            self.year = year
            yield month, year

            month_index += 1

    def iter_urls(self):
        '''Find all the image urls on the current page.
        '''
        # For each month on the dashboard...
        for month, year in self.iter_monthyear():
            # Navigate to the next month.
            month.click()
            self.info("Getting urls for month: %s" % month.text)
            self.sleep(minsleep=5,maxsleep=7)
            re_url = re.compile('\("([^"]+)')
            for div in self.br.find_elements_by_xpath("//li/div"):
                url = re_url.search(div.get_attribute("style"))
                if not url:
                    continue
                url = url.group(1)
                url = url.replace('thumbnail=true', '')
                url = url.replace('&thumbnail=true', '')
                url = 'https://www.tadpoles.com' + url
                yield url

    def save_image(self, url):
        '''Save an image locally using requests.
        '''

        # Make the local filename.
        _, key = url.split("key=")
        filename_parts = ['img', self.year.text, self.month.text, '%s.jpg']
        filename_jpg = abspath(join(*filename_parts) % key)
        
        # We don't know if we have a video or image yet so create both name
        filename_parts = ['img', self.year.text, self.month.text, '%s.mp4']
        filename_video = abspath(join(*filename_parts) % key)

        # Only download if the file doesn't already exist.
        if isfile(filename_jpg):
            self.info("Already downloaded image: %s" % filename_jpg)
            return
        if isfile(filename_video):
            self.info("Already downloaded video: %s" % filename_video)
            return
        else:
            self.info("Download from: %s" % url)
            
        # Make sure the parent dir exists.
        dr = dirname(filename_jpg)
        if not isdir(dr):
            os.makedirs(dr)

        # Download it with requests.
        resp = requests.get(url, cookies=self.req_cookies, stream=True)
        if resp.status_code == 200:
            content_type = resp.headers['content-type']
            self.info("Content Type: %s." % content_type)

            if content_type == 'image/jpeg':
                filename = filename_jpg
            elif content_type == 'video/mp4':
                filename = filename_video
            else:
                self.warning("Unsupported content type: %s" % content_type)
                return
            
            self.info("Saving: %s" % filename)

            with open(filename, 'wb') as f:
                for chunk in resp.iter_content(1024):
                    f.write(chunk)

            self.info("Finished saving %s" % filename)
        else:
            msg = 'Error (%r) downloading %r'
            raise DownloadError(msg % (resp.status_code, url))

    def download_images(self):
        '''Login to tadpoles.com and download all user's images.
        '''
        self.navigate_url(self.ROOT_URL)

        try:
            self.load_cookies()
        except (OSError, IOError) as e:
            self.info("Creating new cookies")
            self.do_login()
            self.dump_cookies()
        else:
            self.add_cookies_to_browser()
            self.navigate_url(self.HOME_URL)

        # Get the cookies ready for requests lib.
        self.requestify_cookies()

        for url in self.iter_urls():
            try:
                self.save_image(url)
            except DownloadError as exc:
                self.exception(exc)

    def main(self):
        self.info("Starting")
        with self as client:
            try:
                client.download_images()
            except Exception as exc:
                self.info(exc)


def download_images():
    Client().main()


if __name__ == "__main__":
    download_images()

