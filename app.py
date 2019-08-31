"""This module downloads all photos/videos from tadpole to a local folder."""

import os
from os.path import abspath, dirname, join, isfile, isdir, exists
import re
import sys
import time
import pickle
import logging
import logging.config

from random import randrange
from getpass import getpass

from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException
import requests

class DownloadError(Exception):
    """An exception indicating some errors during downloading"""
    pass


class Client:
    """The main client class responsible for downloading pictures/videos"""

    COOKIE_FILE = "state/cookies.pkl"
    ROOT_URL = "http://tadpoles.com/"
    HOME_URL = "https://www.tadpoles.com/parents"
    CONFIG_FILE_NAME = "conf.json"
    MIN_SLEEP = 1
    MAX_SLEEP = 3

    def __init__(self):
        self.init_folders()
        self.init_logging()
        self.browser = None
        self.cookies = None
        self.req_cookies = None
        self.__current_month__ = None
        self.__current_year__ = None

    def init_folders(self):
        if not exists('logs'):
            os.makedirs('logs')

    def init_logging(self):
        """Set up logging configuration"""

        logging_config = dict(
            version=1,
            formatters={
                'f': {
                    'format': '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'}
                },
            handlers={
                'h': {
                    'class': 'logging.StreamHandler',
                    'formatter': 'f',
                    'level': logging.DEBUG
                },
                'f': {
                    'class': 'logging.FileHandler',
                    'formatter': 'f',
                    'filename': 'logs/tadpole.log',
                    'level': logging.INFO}
            },
            root={
                'handlers': ['h', 'f'],
                'level': logging.DEBUG,
            },
        )

        logging.config.dictConfig(logging_config)

        self.logger = logging.getLogger('tadpole-catcher')

    def __enter__(self):
        self.logger.info("Starting browser")
        self.browser = webdriver.Chrome(ChromeDriverManager().install())
        self.browser.implicitly_wait(10)
        self.logger.info("Got a browser")
        return self

    def __exit__(self, *args):
        self.logger.info("Shutting down browser")
        self.browser.quit()

    def sleep(self, minsleep=None, maxsleep=None):
        """Sleep a random amount of time bound by the min and max value"""
        _min = minsleep or self.MIN_SLEEP
        _max = maxsleep or self.MAX_SLEEP
        duration = randrange(_min * 100, _max * 100) / 100.0
        self.logger.info('Sleeping %r', duration)
        time.sleep(duration)

    def navigate_url(self, url):
        """Force the browser to go a url"""
        self.logger.info("Navigating to %r", url)
        self.browser.get(url)

    def load_cookies(self):
        """Load cookies from a previously saved ones"""
        self.logger.info("Loading cookies.")
        if not isdir('state'):
            os.makedirs('state')
        with open(self.COOKIE_FILE, "rb") as file:
            self.cookies = pickle.load(file)

    def dump_cookies(self):
        """Save cookies of the existing session to a file"""
        self.logger.info("Dumping cookies.")
        self.cookies = self.browser.get_cookies()
        with open(self.COOKIE_FILE, "wb") as file:
            pickle.dump(self.browser.get_cookies(), file)

    def add_cookies_to_browser(self):
        """Load the saved cookies into the browser"""
        self.logger.info("Adding the cookies to the browser.")
        for cookie in self.cookies:
            if self.browser.current_url.strip('/').endswith(cookie['domain']):
                '''Expiry is somehow not in the right format. Remove it'''
                if 'expiry' in cookie:
                    del cookie['expiry']
                self.browser.add_cookie(cookie)

    def requestify_cookies(self):
        """Transform the cookies to what the request lib requires."""
        self.logger.info("Transforming the cookies for requests lib.")
        self.req_cookies = {}
        for s_cookie in self.cookies:
            self.req_cookies[s_cookie["name"]] = s_cookie["value"]

    def switch_windows(self):
        '''Switch to the other window.'''
        self.logger.info("Switching windows.")
        all_windows = set(self.browser.window_handles)
        current_window = set([self.browser.current_window_handle])
        other_window = (all_windows - current_window).pop()
        self.browser.switch_to.window(other_window)

    def do_login(self):
        """Perform login to tadpole (using google)"""
        self.logger.info("Navigating to login page.")
        self.browser.find_element_by_id("login-button").click()
        self.browser.find_element_by_class_name("tp-block-half").click()
        self.browser.find_element_by_xpath('//img[contains(@data-bind,"click:loginGoogle")]').click()

        # Focus on the google auth popup.
        self.switch_windows()

        # wait while users login
        input("Enter a key when you finished logging in")

        self.activate_browser()

    def iter_monthyear(self):
        '''Yields pairs of xpaths for each year/month tile on the
        right hand side of the user's home page.
        '''
        month_xpath_tmpl = '//*[@id="app"]/div[3]/div[1]/ul/li[%d]/div/div/div/div/span[%d]'
        month_index = 1
        while True:
            month_xpath = month_xpath_tmpl % (month_index, 1)
            year_xpath = month_xpath_tmpl % (month_index, 2)

            # Go home if not there already.
            if self.browser.current_url != self.HOME_URL:
                self.navigate_url(self.HOME_URL)
            try:
                # Find the next month and year elements.
                month = self.browser.find_element_by_xpath(month_xpath)
                year = self.browser.find_element_by_xpath(year_xpath)
            except NoSuchElementException:
                # We reached the end of months on the profile page.
                self.logger.info("No months left to scrape. Stopping.")
                sys.exit(0)

            self.__current_month__ = month
            self.__current_year__ = year
            yield month

            month_index += 1

    def iter_urls(self):
        '''Find all the image urls on the current page.
        '''
        # For each month on the dashboard...
        for month in self.iter_monthyear():
            # Navigate to the next month.
            month.click()
            self.logger.info("Getting urls for month: %s", month.text)
            self.sleep(minsleep=5, maxsleep=7)
            re_url = re.compile('\\("([^"]+)')
            for div in self.browser.find_elements_by_xpath("//li/div"):
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
        year_text = self.__current_year__.text
        month_text = self.__current_month__.text

        filename_parts = ['download', year_text, month_text, 'tadpoles-%s-%s-%s.%s']
        filename_jpg = abspath(join(*filename_parts) % (year_text, month_text, key, 'jpg'))

        # we might even get a png file even though the mime type is jpeg.
        filename_png = abspath(join(*filename_parts) % (year_text, month_text, key, 'png'))

        # We don't know if we have a video or image yet so create both name
        filename_video = abspath(join(*filename_parts) % (year_text, month_text, key, 'mp4'))

        # Only download if the file doesn't already exist.
        if isfile(filename_jpg):
            self.logger.info("Already downloaded image: %s", filename_jpg)
            return
        if isfile(filename_video):
            self.logger.info("Already downloaded video: %s", filename_video)
            return
        if isfile(filename_png):
            self.logger.info("Already downloaded png file: %s", filename_png)
            return

        self.logger.info("Downloading from: %s", url)

        # Make sure the parent dir exists.
        directory = dirname(filename_jpg)
        if not isdir(directory):
            os.makedirs(directory)

        # Sleep to avoid bombarding the server
        self.sleep(1, 3)

        # Download it with requests.
        while True:
            resp = requests.get(url, cookies=self.req_cookies, stream=True)
            if resp.status_code == 200:
                file = None
                try:
                    content_type = resp.headers['content-type']

                    self.logger.info("Content Type: %s.", content_type)

                    if content_type == 'image/jpeg':
                        filename = filename_jpg
                    elif content_type == 'image/png':
                        filename = filename_png
                    elif content_type == 'video/mp4':
                        filename = filename_video
                    else:
                        self.logger.warning("Unsupported content type: %s", content_type)
                        return

                    for chunk in resp.iter_content(1024):
                        if file is None:
                            self.logger.info("Saving: %s", filename)
                            file = open(filename, 'wb')
                        file.write(chunk)

                    self.logger.info("Finished saving %s", filename)
                finally:
                    if file is not None:
                        file.close()
                break
            else:
                msg = 'Error downloading %r. Retrying.'
                self.logger.warning(msg, url)
                self.sleep(1, 5)

    def download_images(self):
        '''Login to tadpoles.com and download all user's images.
        '''
        self.navigate_url(self.ROOT_URL)

        try:
            self.load_cookies()
        except (OSError, IOError):
            self.logger.info("Creating new cookies")
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
            except DownloadError:
                self.logger.exception("Error while saving url %s", url)

if __name__ == "__main__":
    with Client() as client:
        client.download_images()

