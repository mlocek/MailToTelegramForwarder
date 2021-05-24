#!/usr/bin/env python3

try:
    import sys
    import re
    import socket
    import time
    import logging
    import configparser
    import argparse
    import telegram
    from telegram.utils import helpers
    import imaplib
    import email
    from email.header import Header, decode_header, make_header
except ImportError as import_error:
    logging.critical(import_error.__class__.__name__ + ": " + import_error.args[0])
    sys.exit(2)

"""
    Mail2TelegramForwarder:
                    A python script that fetches mails from remote IMAP mail server
                    and forward body and/or attachments to Telegram chat/user.
                    
                    Based on great work done by:
                    https://github.com/florianpaulhoberg/IMAP2Telegram
                    https://github.com/angelos-se/IMAPBot 
"""

__appname__ = "Mail to Telegram Forwarder"
__version__ = "0.1"
__author__ = "Awalon (https://github.com/awalon)"


class Tool:
    def __init__(self,):
        pass

    @staticmethod
    def bin2str(value, **kwargs):
        encoding = kwargs.get('encoding')
        if not encoding:
            encoding = 'utf-8'
        if type(value) is bytes:
            try:
                return str(bytes.decode(value, encoding=encoding, errors='replace'))
            except UnicodeDecodeError as decode_error:
                logging.error("Can not decode value: '", value, "' reason: ", decode_error.reason)
        else:
            return str(value)


class Config:
    config = None

    imap_user = None
    imap_password = None
    imap_server = None
    imap_port = 993
    imap_timeout = 60
    imap_refresh = 10
    imap_push_mode = False
    imap_disconnect = False
    imap_folder = 'INBOX'
    imap_search = '(UID ${lastUID}:* UNSEEN)'
    imap_mark_as_read = False
    imap_max_length = 2000

    tg_bot_token = None
    tg_forward_to_user_id = None
    tg_prefer_html = True
    tg_markdown_version = 2
    tg_forward_mail_content = True
    tg_forward_attachment = True

    def __init__(self, cmd_args):
        """
            Parse config file to obtain login credentials, address of remote mail server,
            telegram config and configuration which controls behaviour of this script .
        """
        try:
            self.config = configparser.ConfigParser()
            files = self.config.read(cmd_args.config)
            if len(files) == 0:
                logging.critical("Error parsing config file: File '%s' not found!" % cmd_args.config)
                sys.exit(2)

            self.imap_user = self.get_config('Mail', 'user', self.imap_user)
            self.imap_password = self.get_config('Mail', 'password', self.imap_password)
            self.imap_server = self.get_config('Mail', 'server', self.imap_server)
            self.imap_port = self.get_config('Mail', 'port', self.imap_port, int)
            self.imap_timeout = self.get_config('Mail', 'timeout', self.imap_timeout, int)
            self.imap_refresh = self.get_config('Mail', 'refresh', self.imap_refresh, int)
            self.imap_push_mode = self.get_config('Mail', 'push_mode', self.imap_push_mode, bool)
            self.imap_disconnect = self.get_config('Mail', 'disconnect', self.imap_disconnect, bool)
            self.imap_folder = self.get_config('Mail', 'folder', self.imap_folder)
            self.imap_search = self.get_config('Mail', 'search', self.imap_search)
            self.imap_mark_as_read = self.get_config('Mail', 'mark_as_read', self.imap_mark_as_read, bool)
            self.imap_max_length = self.get_config('Mail', 'max_length', self.imap_max_length, int)

            self.tg_bot_token = self.get_config('Telegram', 'bot_token', self.tg_bot_token)
            self.tg_forward_to_user_id = self.get_config('Telegram', 'forward_to_user_id',
                                                         self.tg_forward_to_user_id, int)
            self.tg_forward_mail_content = self.get_config('Telegram', 'forward_mail_content',
                                                           self.tg_forward_mail_content, bool)
            self.tg_prefer_html = self.get_config('Telegram', 'prefer_html', self.tg_prefer_html, bool)
            self.tg_markdown_version = self.get_config('Telegram', 'markdown_version', self.tg_markdown_version, int)
            self.tg_forward_attachment = self.get_config('Telegram', 'forward_attachment',
                                                         self.tg_forward_attachment, bool)

        except configparser.ParsingError as parse_error:
            logging.critical("Error parsing config file: Impossible to parse file %s. Message: %s"
                             % (parse_error.source, parse_error.message))
            sys.exit(2)
        except configparser.Error as config_error:
            logging.critical("Error parsing config file: %s." % config_error.message)
            sys.exit(2)

    def get_config(self, section, key, default=None, value_type=None):
        value = default
        try:
            if self.config.has_section(section):
                if self.config.has_option(section, key):
                    if value_type is not None:
                        # get value based on type of default value
                        if value_type is int:
                            value = self.config.getint(section, key)
                        elif value_type is float:
                            value = self.config.getfloat(section, key)
                        elif value_type is bool:
                            value = self.config.getboolean(section, key)
                        else:
                            value = self.config.get(section, key)
                    else:
                        # use string as default
                        value = self.config.get(section, key)
            else:
                # raise exception as both sections are mandatory sections (Mail + Telegram)
                logging.warning("Get config value error for '%s'.'%s' (default: '%s'): Missing section '%s'."
                                % (section, key, default, section))
                raise configparser.NoSectionError(section)

        except configparser.Error as config_error:
            logging.critical("Error parsing config file: %s." % config_error.message)
            raise config_error
        except Exception as get_val_error:
            logging.critical("Get config value error for '%s'.'%s' (default: '%s'): %s."
                             % (section, key, default, get_val_error))
            raise get_val_error

        return value


class TelegramBot:
    config = None

    def __init__(self, config):
        self.config = config

    @staticmethod
    def cleanup_html(message):
        """
        Parse HTML message and remove HTML elements not supported by Telegram
        """
        # supported tags
        # https://core.telegram.org/bots/api#sendmessage
        # <b>bold</b>, <strong>bold</strong>
        # <i>italic</i>, <em>italic</em>
        # <u>underline</u>, <ins>underline</ins>
        # <s>strikethrough</s>, <strike>strikethrough</strike>, <del>strikethrough</del>
        # <b>bold <i>italic bold <s>italic bold strikethrough</s> <u>underline italic bold</u></i> bold</b>
        # <a href="http://www.example.com/">inline URL</a>
        # <a href="tg://user?id=123456789">inline mention of a user</a>
        # <code>inline fixed-width code</code>
        # <pre>pre-formatted fixed-width code block</pre>
        # <pre><code class="language-python">pre-formatted fixed-width code block
        #      written in the Python programming language</code></pre>

        # extract HTML body to get payload from mail
        tg_body = re.sub('.*<body[^>]*>(?P<body>.*)</body>.*$', '\g<body>', message, flags=(re.DOTALL | re.MULTILINE))

        # remove all HTML comments
        tg_body = re.sub(r'<!--.*?-->', '', tg_body, flags=(re.DOTALL | re.MULTILINE))

        # remove attributes from elements but href of "a"- elements
        tg_body = re.sub(r'<\s*?(?P<elem>\w+)\b\s*?[^>]*?(?P<ref>\s+href\s*=\s*"[^"]+")?[^>]*?>',
                         '<\g<elem>\g<ref>>', tg_body, flags=(re.DOTALL | re.MULTILINE))

        # remove style and script elements/blocks
        tg_msg = re.sub(r'<\s*(?P<elem>script|style)\s*>.*?</\s*(?P=elem)\s*>',
                        '', tg_body, flags=(re.DOTALL | re.MULTILINE))

        # remove multiple line breaks and spaces
        tg_msg = re.sub(r'[\r\n]', '', tg_msg)
        tg_msg = re.sub(r'\s[\s]+', ' ', tg_msg).strip()

        # translate paragraphs and line breaks (block elements)
        tg_msg = re.sub(r'</?\s*(?P<elem>(p|div|table|h\d+))\s*>', '\n', tg_msg, flags=re.MULTILINE)
        tg_msg = re.sub(r'</\s*(?P<elem>(tr))\s*>', '\n', tg_msg, flags=re.MULTILINE)
        tg_msg = re.sub(r'</?\s*(br)\s*[^>]*>', '\n', tg_msg, flags=re.MULTILINE)

        # prepare list items (migrate list items to "- <text of li element>")
        tg_msg = re.sub(r'(<\s*[ou]l\s*>[^<]*)?<\s*li\s*>', '\n- ', tg_msg, flags=re.MULTILINE)
        tg_msg = re.sub(r'</\s*li\s*>([^<]*</\s*[ou]l\s*>)?', '\n', tg_msg, flags=re.MULTILINE)

        # remove unsupported tags
        regex_filter_elem = re.compile('<\s*(?!/?(bold|strong|i|em|u|ins|s|strike|del|b|a|code|pre))[^>]*?>',
                                       flags=re.MULTILINE)
        tg_msg = re.sub(regex_filter_elem, ' ', tg_msg)
        tg_msg = re.sub(r'</?\s*(img|span)\s*[^>]*>', '', tg_msg, flags=(re.DOTALL | re.MULTILINE))

        # remove empty links
        tg_msg = re.sub(r'<\s*a\s*>(?P<link>[^<]*)</\s*a\s*>', '\g<link> ', tg_msg, flags=(re.DOTALL | re.MULTILINE))

        return tg_msg

    def send_message(self, mails):
        """
        Send mail data over Telegram API to chat/user.
        """
        try:
            bot = telegram.Bot(self.config.tg_bot_token)
            tg_chat = bot.get_chat(self.config.tg_forward_to_user_id)

            for mail in mails:
                if self.config.tg_markdown_version == 2:
                    parser = telegram.ParseMode.MARKDOWN_V2
                else:
                    parser = telegram.ParseMode.MARKDOWN
                if mail.type == MailData.HTML:
                    parser = telegram.ParseMode.HTML

                if self.config.tg_forward_mail_content or not self.config.tg_forward_attachment:
                    # send mail content (summary)
                    message = mail.summary

                    tg_message = bot.send_message(chat_id=self.config.tg_forward_to_user_id,
                                                  parse_mode=parser,
                                                  text=message,
                                                  disable_web_page_preview=False)

                    logging.info("Mail summary for '%s' was sent with ID '%i' to '%s' (ID: '%i')"
                                 % (mail.mail_subject, tg_message.message_id,
                                    tg_chat.full_name, self.config.tg_forward_to_user_id))

                if self.config.tg_forward_attachment and len(mail.attachments) > 0:
                    for attachment in mail.attachments:
                        subject = mail.mail_subject
                        if mail.type == MailData.HTML:
                            file_name = attachment.name
                            caption = '<b>' + subject + '</b>:\n' + file_name
                        else:
                            file_name = telegram.utils.helpers.escape_markdown(
                                text=attachment.name, version=self.config.tg_markdown_version)
                            caption = '*' + subject + '*:\n' + file_name

                        tg_message = bot.send_document(chat_id=self.config.tg_forward_to_user_id,
                                                       parse_mode=parser,
                                                       caption=caption,
                                                       document=attachment.file,
                                                       filename=attachment.name,
                                                       disable_content_type_detection=False)

                        logging.info("Attachment '%s' was sent with ID '%i' to '%s' (ID: '%i')"
                                     % (attachment.name, tg_message.message_id,
                                        tg_chat.full_name, self.config.tg_forward_to_user_id))

        except telegram.TelegramError as tg_error:
            logging.critical("Failed to send Telegram message: " + tg_error.message)
            return False

        except Exception as send_error:
            logging.critical("Failed to send Telegram message: " + send_error.args[0])
            return False

        return True


class MailAttachment:
    idx = 0
    name = ''
    file = None


class MailBody:
    text = ''
    html = ''
    attachments = [MailAttachment]


class MailData:
    TEXT = 1
    HTML = 2

    uid = ''
    raw = ''
    type = TEXT
    summary = ''
    mail_from = ''
    mail_subject = ''
    mail_body = ''
    attachment_summary = ''
    attachments = [MailAttachment]


class Mail:
    mailbox = None
    config = None
    last_uid = None

    class MailError(Exception):
        def __init__(self, message, errors=None):
            super().__init__(message)
            self.errors = errors

    def __init__(self, config):
        """
        Login to remote IMAP server.
        """
        self.config = config
        try:
            self.mailbox = imaplib.IMAP4_SSL(host=config.imap_server,
                                             port=config.imap_port,
                                             timeout=config.imap_timeout)
            rv, data = self.mailbox.login(config.imap_user, config.imap_password)
            if rv != 'OK':
                return

        except socket.gaierror as gai_error:
            msg = "Connection error '%s:%i': %s" % (config.imap_server,
                                                    config.imap_port,
                                                    gai_error.strerror)
            logging.error(msg)
            raise self.MailError(msg, gai_error)

        except imaplib.IMAP4_SSL.error as imap_ssl_error:
            error_msgs = [Tool.bin2str(arg) for arg in imap_ssl_error.args]
            msg = "Login to '%s:%i' failed: %s" % (config.imap_server,
                                                   config.imap_port,
                                                   ', '.join(error_msgs))
            logging.error(msg)
            raise self.MailError(msg, imap_ssl_error)

        except Exception as login_error:
            msg = "Mail error during connection to '%s:%i' failed: %s" \
                  % (config.imap_server, config.imap_port, ', '.join(map(str, login_error.args)))
            logging.error(msg)
            raise self.MailError(msg, login_error)

        rv, mailboxes = self.mailbox.list()
        if rv != 'OK':
            self.disconnect()
            msg = "Can't get list of available mailboxes / folders: %s" % str(rv)
            raise self.MailError(msg)
        else:
            logging.info("Mailboxes:")
            logging.info(mailboxes)

        rv, data = self.mailbox.select(config.imap_folder)
        if rv == 'OK':
            logging.info("Processing mailbox...\n")
        else:
            msg = "ERROR: Unable to open mailbox: %s" % str(rv)
            logging.error(msg)
            raise self.MailError(msg)

    def disconnect(self):
        if self.mailbox is not None:
            self.mailbox.close()
            self.mailbox.logout()
            self.mailbox = None

    @staticmethod
    def decode_body(msg) -> MailBody:
        """
        Get payload from message and return structured body data
        """
        html = None
        text = None
        attachments = []
        index = 1

        for part in msg.walk():
            if part.get_content_type().startswith('multipart/'):
                continue

            elif part.get_content_type() == 'text/plain':
                text = part.get_payload(decode=True)
                encoding = part.get_content_charset()
                if not encoding:
                    encoding = 'utf-8'
                text = bytes(text).decode(encoding).strip()

            elif part.get_content_type() == 'text/html':
                html = part.get_payload(decode=True)
                encoding = part.get_content_charset()
                if not encoding:
                    encoding = 'utf-8'
                html = bytes(html).decode(encoding).strip()

            elif part.get_content_charset() is None and part.get_content_disposition() == 'attachment':
                attachment = MailAttachment()
                attachment.idx = index
                attachment.name = str(part.get_filename())
                attachment.file = part.get_payload(decode=True)
                attachments.append(attachment)
                index += 1

        body = MailBody()
        body.text = text
        body.html = html
        body.attachments = attachments
        return body

    def get_last_uid(self):
        """
        get UID of most recent mail
        """
        rv, data = self.mailbox.uid('search', '', 'UID *')
        if rv != 'OK':
            logging.info("No messages found!")
            return
        return bytes(data[0]).decode()

    def parse_mail(self, uid, mail):
        """
        parse data from mail like subject, body and attachments and return structured mail data
        """
        try:
            msg = email.message_from_bytes(mail)

            # decode body data (text, html, multipart/attachments)
            body = self.decode_body(msg)
            message_type = MailData.TEXT
            content = ''

            if self.config.tg_forward_mail_content:
                # remove useless content
                content = body.text.replace('()', '').replace('[]', '').strip()

                if self.config.tg_prefer_html:
                    # Prefer HTML
                    if body.html:
                        message_type = MailData.HTML
                        content = TelegramBot.cleanup_html(body.html)

                    elif body.text:
                        content = telegram.utils.helpers.escape_markdown(text=content,
                                                                         version=self.config.tg_markdown_version)

                else:
                    if body.text:
                        content = telegram.utils.helpers.escape_markdown(text=content,
                                                                         version=self.config.tg_markdown_version)

                    elif body.html:
                        message_type = MailData.HTML
                        content = TelegramBot.cleanup_html(body.html)

                if content:
                    # remove multiple line breaks (keeping up to 1 empty line)
                    content = re.sub(r'(\s*\r?\n){2,}', "\n\n", content)

                    if message_type == MailData.HTML:
                        # add space after links (provide space for touch on link lists)
                        content = re.sub(r'(?P<a></a>)\s*', '\g<a>\n\n', content, flags=re.MULTILINE)

                    # remove spaces and line breaks on start and end (enhanced strip)
                    content = re.sub(r'^[\s\n]*', '', content)
                    content = re.sub(r'[\s\n]*$', '', content)

                    max_len = self.config.imap_max_length
                    if len(content) > max_len:
                        content = content[:max_len] \
                                  + "... (first " + str(max_len) + " characters)"

            # attachment summary
            attachments_summary = ""
            if body.attachments:
                if message_type == MailData.HTML:
                    attachments_summary = "\n\n" + chr(10133) + \
                                          " <b>" + str(len(body.attachments)) + " attachments:</b>\n"
                else:
                    attachments_summary = "\n\n" + chr(10133) + \
                                          " **" + str(len(body.attachments)) + " attachments:**\n"
                for attachment in body.attachments:
                    file_name = telegram.utils.helpers.escape_markdown(
                        text=attachment.name, version=self.config.tg_markdown_version)
                    attachments_summary += "\n " + str(attachment.idx) + ": " + file_name

            # subject
            subject = ''
            for subject_part in email.header.decode_header(msg['Subject']):
                part, encoding = subject_part
                subject += Tool.bin2str(part, encoding=encoding)

            # build summary
            mail_from = Tool.bin2str(msg['From'])
            if self.config.tg_forward_mail_content:
                summary_line = "\n=============================\n"
            else:
                summary_line = "\n"

            if message_type == MailData.HTML:
                email_text = "<b>From:</b> " + mail_from + "\n<b>Subject:</b> "
            else:
                subject = telegram.utils.helpers.escape_markdown(text=subject,
                                                                 version=self.config.tg_markdown_version)
                mail_from = telegram.utils.helpers.escape_markdown(text=mail_from,
                                                                   version=self.config.tg_markdown_version)
                summary_line = telegram.utils.helpers.escape_markdown(text=summary_line,
                                                                      version=self.config.tg_markdown_version)
                email_text = "*From:* " + mail_from + "\n*Subject:* "
            email_text += subject + summary_line + content + " " + attachments_summary

            mail_data = MailData()
            mail_data.uid = uid
            mail_data.raw = msg
            mail_data.type = message_type
            mail_data.mail_from = mail_from
            mail_data.mail_subject = subject
            mail_data.mail_body = content
            mail_data.summary = email_text
            mail_data.attachment_summary = email_text
            mail_data.attachments = body.attachments

            return mail_data

        except Exception as parse_error:
            if len(parse_error.args) > 0:
                logging.critical("Cannot process mail: " + parse_error.args[0])
            else:
                logging.critical("Cannot process mail: " + parse_error.__str__())
            return None

    def search_mails(self):
        """
        Search mail on remote IMAP server and return list of parsed mails.
        """
        if self.last_uid is None:
            self.last_uid = self.get_last_uid()

        # build IMAP search string
        search_string = self.config.imap_search
        if not search_string:
            "(UID " + str(self.last_uid) + ":*)"
        else:
            search_string = re.sub(r'\${lastUID}', str(self.last_uid), search_string)

        rv, data = self.mailbox.uid('search', '', search_string)
        if rv != 'OK':
            logging.info("No messages found!")
            return

        mails = []
        max_num = self.last_uid
        for num in sorted(data[0].split()):
            try:
                rv, data = self.mailbox.uid('fetch', num, '(RFC822)')
                if rv != 'OK':
                    logging.error("ERROR getting message", num)
                    return

                if len(data) > 0:
                    msg_raw = data[0][1]
                    mail = self.parse_mail(Tool.bin2str(num), msg_raw)
                    mails.append(mail)

            except Exception as mail_error:
                logging.critical("Cannot process mail: %s" % ', '.join(map(str, mail_error.args)))

            finally:
                # remember new UID for next loop
                max_num = num

        if len(mails) > 0:
            self.last_uid = Tool.bin2str(max_num)
            logging.info("Got %i new mail(s( to forward, changed UID to %s" % (len(mails), self.last_uid))
        return mails


class SystemdHandler(logging.Handler):
    """
        Class to handle logging options.
    """
    PREFIX = {
        logging.CRITICAL: "<2> " + __appname__ + ": ",
        logging.ERROR: "<3> " + __appname__ + ": ",
        logging.WARNING: "<4> " + __appname__ + ": ",
        logging.INFO: "<6> " + __appname__ + ": ",
        logging.DEBUG: "<7> " + __appname__ + ": ",
        logging.NOTSET: "<7> " + __appname__ + ": ",
    }

    def __init__(self, stream=sys.stdout):
        self.stream = stream
        logging.Handler.__init__(self)

    def emit(self, record):
        try:
            msg = self.PREFIX[record.levelno] + self.format(record) + "\n"
            self.stream.write(msg)
            self.stream.flush()
        except Exception as emit_error:
            self.handleError(record)
            if len(emit_error.args) > 0:
                print("ERROR: SystemdHandler.emit failed with: " + emit_error.args[0])
            else:
                print("ERROR: SystemdHandler.emit failed with: " + emit_error.__str__())


def main():
    """
        Run the main program
    """
    root_logger = logging.getLogger()
    root_logger.setLevel("INFO")
    root_logger.addHandler(SystemdHandler())

    args_parser = argparse.ArgumentParser(description='Mail to Telegram Forwarder')
    args_parser.add_argument('-c', '--config', type=str, help='Path to config file', required=True)
    cmd_args = args_parser.parse_args()

    if cmd_args.config is None:
        logging.warning("Could not load config file, as no config file was provided.")
        sys.exit(2)

    mailbox = None
    try:
        config = Config(cmd_args)
        tg_bot = TelegramBot(config)
        mailbox = Mail(config)

        # Keep polling
        while True:
            if mailbox is None:
                mailbox = Mail(config)

            mails = mailbox.search_mails()

            if config.imap_disconnect:
                # if not reuse previous connection
                mailbox.disconnect()

            # send mail data via TG bot
            if len(mails) > 0:
                tg_bot.send_message(mails)

            if config.imap_push_mode:
                logging.info("IMAP IDLE mode")
            else:
                time.sleep(float(config.imap_refresh))

    except KeyboardInterrupt:
        logging.critical('Stopping user aborted with CTRL+C')

    except Mail.MailError:  # ignore errors already handled by Mail- Class
        pass

    except Exception as main_error:
        if len(main_error.args) > 0:
            logging.critical('Error occurred: %s' % ', '.join(map(str, main_error.args)))
        else:
            logging.critical('Error occurred: ' + main_error.__str__())

    finally:
        if mailbox is not None:
            mailbox.disconnect()
        logging.info('Mail to Telegram Forwarder stopped!')


main()
